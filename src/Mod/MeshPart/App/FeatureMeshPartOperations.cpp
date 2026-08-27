// SPDX-License-Identifier: LGPL-2.1-or-later

#include "FeatureMeshPartOperations.h"
#include "MeshSolidShape.h"

#include <cmath>
#include <initializer_list>
#include <iterator>
#include <limits>
#include <memory>
#include <numbers>
#include <string>
#include <utility>
#include <vector>

#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRep_Builder.hxx>
#include <GeomAbs_Shape.hxx>
#include <Geom_BSplineCurve.hxx>
#include <GeomAPI_PointsToBSpline.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Wire.hxx>
#include <TColgp_Array1OfPnt.hxx>

#include <App/ComplexGeoData.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeature.h>
#include <Base/Converter.h>
#include <Base/Exception.h>
#include <Base/Matrix.h>
#include <Base/Tools.h>
#include <Mod/Mesh/App/Mesh.h>
#include <Mod/Mesh/App/Core/Algorithm.h>
#include <Mod/Mesh/App/Core/Grid.h>
#include <Mod/Mesh/App/Core/Projection.h>
#include <Mod/Part/App/FaceMakerCheese.h>
#include <Mod/Part/App/TopoShape.h>

namespace
{

bool operationSuppressed(const App::SuppressibleExtension& extension, const App::DocumentObject& operation)
{
    if (extension.Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(operation.getDocument());
    return timeline && !timeline->isOperationActive(&operation);
}

bool isLiveSource(const App::DocumentObject* source, const App::DocumentObject& result)
{
    const auto* document = result.getDocument();
    return source && source != &result && document && source->getDocument() == document
        && source->getNameInDocument() && document->containsObject(source)
        && App::DocumentTimeline::isObjectUsableAtCurrentPosition(source);
}

bool isExplicitlySuppressed(const App::DocumentObject* object)
{
    const auto* extension = object ? object->getExtensionByType<App::SuppressibleExtension>(true)
                                   : nullptr;
    return extension && extension->Suppressed.getValue();
}

const Mesh::Feature* linkedMesh(
    const App::PropertyLink& link,
    const App::DocumentObject& result,
    const char* propertyName
)
{
    const auto* source = freecad_cast<const Mesh::Feature*>(link.getValue());
    if (!isLiveSource(source, result)) {
        throw Base::ValueError(
            std::string(propertyName) + " must link to a live mesh in this document"
        );
    }
    if (isExplicitlySuppressed(source)) {
        throw Base::ValueError(std::string(propertyName) + " is suppressed");
    }
    if (source->Mesh.getValue().countFacets() == 0) {
        throw Base::ValueError(std::string(propertyName) + " contains no facets");
    }
    return source;
}

bool sourcePropertyTouched(const App::PropertyLink& link)
{
    auto* source = link.getValue();
    auto* mesh = source ? source->getPropertyByName("Mesh") : nullptr;
    auto* shape = source ? source->getPropertyByName("Shape") : nullptr;
    auto* placement = source ? source->getPropertyByName("Placement") : nullptr;
    return (source && source->isTouched()) || (mesh && mesh->isTouched())
        || (shape && shape->isTouched()) || (placement && placement->isTouched());
}

bool sameMeshTopology(const Mesh::MeshObject& first, const Mesh::MeshObject& second)
{
    const auto& firstKernel = first.getKernel();
    const auto& secondKernel = second.getKernel();
    if (firstKernel.CountPoints() != secondKernel.CountPoints()) {
        return false;
    }
    const auto& firstFacets = firstKernel.GetFacets();
    const auto& secondFacets = secondKernel.GetFacets();
    if (firstFacets.size() != secondFacets.size()) {
        return false;
    }
    for (std::size_t index = 0; index < firstFacets.size(); ++index) {
        const auto& left = firstFacets[index];
        const auto& right = secondFacets[index];
        if (left._aulPoints[0] != right._aulPoints[0]
            || left._aulPoints[1] != right._aulPoints[1]
            || left._aulPoints[2] != right._aulPoints[2]) {
            return false;
        }
    }
    return true;
}

Base::Vector3d transformDirection(const Base::Matrix4D& transform, const Base::Vector3d& direction)
{
    return Base::Vector3d(
        transform[0][0] * direction.x + transform[0][1] * direction.y + transform[0][2] * direction.z,
        transform[1][0] * direction.x + transform[1][1] * direction.y + transform[1][2] * direction.z,
        transform[2][0] * direction.x + transform[2][1] * direction.y + transform[2][2] * direction.z
    );
}

GeomAbs_Shape continuityFromIndex(long index)
{
    switch (index) {
        case 0:
            return GeomAbs_C0;
        case 1:
            return GeomAbs_C1;
        case 2:
            return GeomAbs_C2;
        case 3:
            return GeomAbs_C3;
        default:
            throw Base::ValueError("Continuity must be C0, C1, C2, or C3");
    }
}

Part::TopoShape makePolylineCompound(App::Document& document, const Mesh::MeshObject::TPolylines& polylines)
{
    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);
    bool hasWire = false;
    for (const auto& polyline : polylines) {
        if (polyline.size() < 2) {
            continue;
        }
        BRepBuilderAPI_MakePolygon polygon;
        for (const auto& point : polyline) {
            polygon.Add(gp_Pnt(point.x, point.y, point.z));
        }
        if (polygon.IsDone()) {
            const TopoDS_Wire wire = polygon.Wire();
            if (!wire.IsNull()) {
                builder.Add(compound, wire);
                hasWire = true;
            }
        }
    }
    if (!hasWire) {
        return {};
    }
    return Part::TopoShape(compound, 0, document.getStringHasher());
}

Part::TopoShape makeSectionShape(
    App::Document& document,
    const Mesh::MeshObject& mesh,
    const std::vector<Mesh::MeshObject::TPlane>& planes,
    double epsilon,
    bool connectEdges
)
{
    std::vector<Mesh::MeshObject::TPolylines> sections;
    mesh.crossSections(planes, sections, static_cast<float>(epsilon), connectEdges);

    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);
    bool hasWire = false;
    for (const auto& section : sections) {
        Part::TopoShape sectionShape = makePolylineCompound(document, section);
        if (!sectionShape.isNull()) {
            builder.Add(compound, sectionShape.getShape());
            hasWire = true;
        }
    }
    if (!hasWire) {
        return {};
    }
    return Part::TopoShape(compound, 0, document.getStringHasher());
}

}  // namespace


namespace MeshPart
{

PROPERTY_SOURCE(MeshPart::MeshFromShape, Mesh::Feature)

const char* MeshFromShape::MethodEnums[] = {
    "Standard",
    "Mefisto",
    "Netgen",
    "Gmsh",
    nullptr,
};

MeshFromShape::MeshFromShape()
{
    suppressibleExt.initExtension(this);

    ADD_PROPERTY_TYPE(Source, (nullptr), "Source", App::Prop_None, "Exact source shape or selected subshape");
    ADD_PROPERTY_TYPE(Method, (0L), "Meshing", App::Prop_None, "Native tessellation algorithm");
    Method.setEnums(MethodEnums);
    ADD_PROPERTY_TYPE(
        LinearDeflection,
        (0.1),
        "Meshing",
        App::Prop_None,
        "Maximum linear tessellation deflection"
    );
    ADD_PROPERTY_TYPE(
        AngularDeflection,
        (0.5),
        "Meshing",
        App::Prop_None,
        "Maximum angular tessellation deflection in radians"
    );
    ADD_PROPERTY_TYPE(
        Relative,
        (false),
        "Meshing",
        App::Prop_None,
        "Interpret LinearDeflection relative to the source size"
    );
    ADD_PROPERTY_TYPE(Segments, (false), "Meshing", App::Prop_None, "Create mesh segments for source faces");
    ADD_PROPERTY_TYPE(
        MaximumEdgeLength,
        (0.0),
        "Mefisto",
        App::Prop_None,
        "Maximum edge length; zero uses the mesher default"
    );
    ADD_PROPERTY_TYPE(
        Fineness,
        (2),
        "Netgen",
        App::Prop_None,
        "Netgen fineness preset; values above four use custom settings"
    );
    ADD_PROPERTY_TYPE(GrowthRate, (0.3F), "Netgen", App::Prop_None, "Custom Netgen element growth rate");
    ADD_PROPERTY_TYPE(SegmentsPerEdge, (1.0F), "Netgen", App::Prop_None, "Custom Netgen elements per edge");
    ADD_PROPERTY_TYPE(
        SegmentsPerRadius,
        (2.0F),
        "Netgen",
        App::Prop_None,
        "Custom Netgen elements per curvature radius"
    );
    ADD_PROPERTY_TYPE(
        SecondOrder,
        (false),
        "Netgen",
        App::Prop_None,
        "Generate second-order Netgen elements"
    );
    ADD_PROPERTY_TYPE(
        Optimize,
        (true),
        "Netgen",
        App::Prop_None,
        "Optimize the generated Netgen surface mesh"
    );
    ADD_PROPERTY_TYPE(QuadDominated, (false), "Netgen", App::Prop_None, "Allow quad-dominated Netgen output");
    ADD_PROPERTY_TYPE(
        GmshAlgorithm,
        (2),
        "Gmsh",
        App::Prop_None,
        "Gmsh two-dimensional meshing algorithm identifier"
    );
    ADD_PROPERTY_TYPE(GmshMinimumSize, (0.0), "Gmsh", App::Prop_None, "Minimum Gmsh element size");
    ADD_PROPERTY_TYPE(
        GmshMaximumSize,
        (0.0),
        "Gmsh",
        App::Prop_None,
        "Maximum Gmsh element size; zero leaves it unbounded"
    );
    ADD_PROPERTY_TYPE(
        GmshGeometryTolerance,
        (1.0e-6),
        "Gmsh",
        App::Prop_None,
        "Gmsh geometry and node-merging tolerance"
    );
    ADD_PROPERTY_TYPE(GmshElementOrder, (2), "Gmsh", App::Prop_None, "Gmsh element order");
    static const App::PropertyIntegerConstraint::Constraints orderRange = {
        1,
        2,
        1,
    };
    GmshElementOrder.setConstraints(&orderRange);
    ADD_PROPERTY_TYPE(
        GmshOptimize,
        (true),
        "Gmsh",
        App::Prop_None,
        "Optimize the generated Gmsh surface mesh"
    );
    ADD_PROPERTY_TYPE(
        GmshExecutable,
        ("gmsh"),
        "Gmsh",
        App::Prop_None,
        "Gmsh executable used by the background tessellation job"
    );
    ADD_PROPERTY_TYPE(
        GmshTimeoutSeconds,
        (600),
        "Gmsh",
        App::Prop_None,
        "Maximum time allowed for the background Gmsh job"
    );
    static const App::PropertyIntegerConstraint::Constraints timeoutRange = {
        1,
        86400,
        1,
    };
    GmshTimeoutSeconds.setConstraints(&timeoutRange);
    ADD_PROPERTY_TYPE(
        CachedGmshSourceBrep,
        (""),
        "Gmsh Cache",
        static_cast<App::PropertyType>(App::Prop_Hidden | App::Prop_NoRecompute),
        "Exact BREP corresponding to the accepted Gmsh cache"
    );
    ADD_PROPERTY_TYPE(
        CachedGmshResult,
        (Mesh::MeshObject()),
        "Gmsh Cache",
        static_cast<App::PropertyType>(App::Prop_Hidden | App::Prop_NoRecompute),
        "Accepted Gmsh result reused until source or settings change"
    );
    CachedGmshSourceBrep.setStatus(App::Property::Hidden, true);
    CachedGmshSourceBrep.setStatus(App::Property::NoRecompute, true);
    CachedGmshResult.setStatus(App::Property::Hidden, true);
    CachedGmshResult.setStatus(App::Property::NoRecompute, true);

    static const App::PropertyFloatConstraint::Constraints angularRange = {
        0.0,
        std::numbers::pi,
        0.01,
    };
    static const App::PropertyFloatConstraint::Constraints nonNegative = {
        0.0,
        std::numeric_limits<float>::max(),
        0.1,
    };
    AngularDeflection.setConstraints(&angularRange);
    GrowthRate.setConstraints(&nonNegative);
    SegmentsPerEdge.setConstraints(&nonNegative);
    SegmentsPerRadius.setConstraints(&nonNegative);
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (false),
        "Source",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Background tessellation snapshot state"
    );
    Source.setScope(App::LinkScope::Global);
    for (App::Property* property : std::initializer_list<App::Property*>{
             &Source,
             &Method,
             &LinearDeflection,
             &AngularDeflection,
             &Relative,
             &Segments,
             &MaximumEdgeLength,
             &Fineness,
             &GrowthRate,
             &SegmentsPerEdge,
             &SegmentsPerRadius,
             &SecondOrder,
             &Optimize,
             &QuadDominated,
             &GmshAlgorithm,
             &GmshMinimumSize,
             &GmshMaximumSize,
             &GmshGeometryTolerance,
             &GmshElementOrder,
             &GmshOptimize,
             &GmshExecutable,
             &GmshTimeoutSeconds,
             &CachedGmshSourceBrep,
             &CachedGmshResult,
             &UpdateFromSource,
         }) {
        property->setStatus(App::Property::ReadOnly, true);
    }
    UpdateFromSource.setStatus(App::Property::Hidden, true);
}

bool MeshFromShape::isSuppressed() const
{
    return operationSuppressed(suppressibleExt, *this);
}

short MeshFromShape::mustExecute() const
{
    auto* source = Source.getValue();
    auto* shape = source ? source->getPropertyByName("Shape") : nullptr;
    auto* placement = source ? source->getPropertyByName("Placement") : nullptr;
    if (Source.isTouched() || Method.isTouched() || LinearDeflection.isTouched()
        || AngularDeflection.isTouched() || Relative.isTouched() || Segments.isTouched()
        || MaximumEdgeLength.isTouched() || Fineness.isTouched() || GrowthRate.isTouched()
        || SegmentsPerEdge.isTouched() || SegmentsPerRadius.isTouched() || SecondOrder.isTouched()
        || Optimize.isTouched() || QuadDominated.isTouched() || GmshAlgorithm.isTouched()
        || GmshMinimumSize.isTouched() || GmshMaximumSize.isTouched()
        || GmshGeometryTolerance.isTouched() || GmshElementOrder.isTouched()
        || GmshOptimize.isTouched() || GmshExecutable.isTouched() || GmshTimeoutSeconds.isTouched()
        || UpdateFromSource.isTouched()
        || suppressibleExt.Suppressed.isTouched() || (source && source->isTouched())
        || (shape && shape->isTouched()) || (placement && placement->isTouched())) {
        return 1;
    }
    return Mesh::Feature::mustExecute();
}

App::DocumentObjectExecReturn* MeshFromShape::execute()
{
    try {
        if (isSuppressed()) {
            return App::DocumentObject::StdReturn;
        }
        if (UpdateFromSource.getValue()) {
            throw Base::RuntimeError(
                "Mesh From Shape regeneration must run through the background command"
            );
        }
        if (Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError("Mesh From Shape has no prepared mesh result");
        }
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}


PROPERTY_SOURCE(MeshPart::ShapeFromMesh, Part::Feature)

ShapeFromMesh::ShapeFromMesh()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(Source, (nullptr), "Conversion", App::Prop_None, "Linked source mesh");
    ADD_PROPERTY_TYPE(
        Tolerance,
        (0.1),
        "Conversion",
        App::Prop_None,
        "Tolerance used to build and optionally sew OCC faces"
    );
    ADD_PROPERTY_TYPE(SewShape, (false), "Conversion", App::Prop_None, "Sew adjacent generated faces");
    ADD_PROPERTY_TYPE(
        MakeSolid,
        (false),
        "Conversion",
        App::Prop_None,
        "Build validated solid volumes from closed sewn shells"
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Conversion",
        App::Prop_None,
        "Rebuild the shape when the linked source mesh changes"
    );
    Source.setScope(App::LinkScope::Global);
}

bool ShapeFromMesh::isSuppressed() const
{
    return operationSuppressed(suppressibleExt, *this);
}

short ShapeFromMesh::mustExecute() const
{
    if (Source.isTouched() || Tolerance.isTouched() || SewShape.isTouched()
        || MakeSolid.isTouched() || UpdateFromSource.isTouched()
        || suppressibleExt.Suppressed.isTouched() || sourcePropertyTouched(Source)) {
        return 1;
    }
    return Part::Feature::mustExecute();
}

App::DocumentObjectExecReturn* ShapeFromMesh::execute()
{
    try {
        if (isSuppressed()) {
            if (UpdateFromSource.getValue()) {
                Shape.setValue(Part::TopoShape());
            }
            return App::DocumentObject::StdReturn;
        }
        if (!UpdateFromSource.getValue()) {
            const Part::TopoShape current = Shape.getValue();
            if (current.isNull()) {
                throw Base::RuntimeError("The detached mesh conversion has no cached shape");
            }
            // The process-isolated conversion worker validated the exact BREP
            // before publication.  Repeating OCC's full validity traversal in
            // a document recompute would block the GUI for large meshes.
            return App::DocumentObject::StdReturn;
        }
        const auto* source = linkedMesh(Source, *this, "Source");
        const double tolerance = Tolerance.getValue();
        if (!std::isfinite(tolerance) || tolerance < Precision::Confusion()) {
            throw Base::ValueError("Tolerance must be finite and no smaller than the OCC "
                                   "confusion tolerance");
        }

        Part::TopoShape result(0, getDocument()->getStringHasher());
        if (MakeSolid.getValue()) {
            if (!SewShape.getValue()) {
                throw Base::ValueError("MakeSolid requires SewShape");
            }
            result.setShape(MeshPart::solidShapeFromMesh(*source, tolerance, "Source"));
        }
        else {
            std::vector<Base::Vector3d> points;
            std::vector<Data::ComplexGeoData::Facet> facets;
            source->Mesh.getValue().getFaces(points, facets, 0.0);
            result.setFaces(points, facets, tolerance);
            if (SewShape.getValue()) {
                result.sewShape(tolerance);
            }
        }
        result = result.makeElementRefine();
        if (result.isNull() || !result.isValid()) {
            throw Base::RuntimeError("Mesh conversion produced an invalid shape");
        }
        Shape.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}


PROPERTY_SOURCE(MeshPart::SectionByPlane, Part::Feature)

SectionByPlane::SectionByPlane()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(Source, (nullptr), "Section", App::Prop_None, "Linked source mesh");
    ADD_PROPERTY_TYPE(Plane, (nullptr), "Section", App::Prop_None, "Linked datum plane");
    ADD_PROPERTY_TYPE(
        MinimumLength,
        (1.0e-7),
        "Section",
        App::Prop_None,
        "Minimum retained section-polyline length"
    );
    ADD_PROPERTY_TYPE(
        ConnectEdges,
        (true),
        "Section",
        App::Prop_None,
        "Connect adjacent intersection edges into polylines"
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Section",
        App::Prop_None,
        "Rebuild the section when the linked source or plane changes"
    );
    Source.setScope(App::LinkScope::Global);
    Plane.setScope(App::LinkScope::Global);
}

bool SectionByPlane::isSuppressed() const
{
    return operationSuppressed(suppressibleExt, *this);
}

short SectionByPlane::mustExecute() const
{
    if (Source.isTouched() || Plane.isTouched() || MinimumLength.isTouched()
        || ConnectEdges.isTouched() || UpdateFromSource.isTouched()
        || suppressibleExt.Suppressed.isTouched()
        || sourcePropertyTouched(Source) || sourcePropertyTouched(Plane)) {
        return 1;
    }
    return Part::Feature::mustExecute();
}

App::DocumentObjectExecReturn* SectionByPlane::execute()
{
    try {
        if (isSuppressed()) {
            if (UpdateFromSource.getValue()) {
                Shape.setValue(Part::TopoShape());
            }
            return App::DocumentObject::StdReturn;
        }
        if (!UpdateFromSource.getValue()) {
            if (Shape.getShape().isNull()) {
                throw Base::RuntimeError("The detached plane section has no cached shape");
            }
            return App::DocumentObject::StdReturn;
        }
        const auto* source = linkedMesh(Source, *this, "Source");
        const auto* plane = freecad_cast<const App::GeoFeature*>(Plane.getValue());
        if (!isLiveSource(plane, *this) || isExplicitlySuppressed(plane)) {
            throw Base::ValueError("Plane must link to a live, unsuppressed datum plane in "
                                   "this document");
        }
        const double minimumLength = MinimumLength.getValue();
        if (!std::isfinite(minimumLength) || minimumLength < 0.0) {
            throw Base::ValueError("MinimumLength must be finite and non-negative");
        }

        const Base::Placement placement = plane->Placement.getValue();
        Base::Vector3d normal(0.0, 0.0, 1.0);
        placement.getRotation().multVec(normal, normal);
        if (normal.Length() <= Precision::Confusion()) {
            throw Base::ValueError("Plane has an invalid normal");
        }
        normal.Normalize();

        Mesh::MeshObject::TPlane meshPlane {
            Base::convertTo<Base::Vector3f>(placement.getPosition()),
            Base::convertTo<Base::Vector3f>(normal),
        };
        Part::TopoShape result = makeSectionShape(
            *getDocument(),
            source->Mesh.getValue(),
            {meshPlane},
            minimumLength,
            ConnectEdges.getValue()
        );
        if (result.isNull() || !result.isValid()) {
            throw Base::RuntimeError("The plane does not intersect the mesh");
        }
        Shape.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}


PROPERTY_SOURCE(MeshPart::CrossSections, Part::Feature)

CrossSections::CrossSections()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(Source, (nullptr), "Cross Sections", App::Prop_None, "Linked source mesh");
    ADD_PROPERTY_TYPE(
        PlaneNormal,
        (Base::Vector3d(0.0, 0.0, 1.0)),
        "Cross Sections",
        App::Prop_None,
        "Normal shared by every section plane"
    );
    ADD_PROPERTY_TYPE(
        PlanePositions,
        (std::vector<double> {}),
        "Cross Sections",
        App::Prop_None,
        "Signed section-plane distances along PlaneNormal"
    );
    ADD_PROPERTY_TYPE(
        Epsilon,
        (1.0e-7),
        "Cross Sections",
        App::Prop_None,
        "Intersection and edge-connection tolerance"
    );
    ADD_PROPERTY_TYPE(
        ConnectEdges,
        (true),
        "Cross Sections",
        App::Prop_None,
        "Connect adjacent intersection edges into polylines"
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Cross Sections",
        App::Prop_None,
        "Rebuild the cross-sections when the linked source changes"
    );
    Source.setScope(App::LinkScope::Global);
}

bool CrossSections::isSuppressed() const
{
    return operationSuppressed(suppressibleExt, *this);
}

short CrossSections::mustExecute() const
{
    if (Source.isTouched() || PlaneNormal.isTouched() || PlanePositions.isTouched()
        || Epsilon.isTouched() || ConnectEdges.isTouched() || UpdateFromSource.isTouched()
        || suppressibleExt.Suppressed.isTouched()
        || sourcePropertyTouched(Source)) {
        return 1;
    }
    return Part::Feature::mustExecute();
}

App::DocumentObjectExecReturn* CrossSections::execute()
{
    try {
        if (isSuppressed()) {
            if (UpdateFromSource.getValue()) {
                Shape.setValue(Part::TopoShape());
            }
            return App::DocumentObject::StdReturn;
        }
        if (!UpdateFromSource.getValue()) {
            if (Shape.getShape().isNull()) {
                throw Base::RuntimeError("The detached cross-sections have no cached shape");
            }
            return App::DocumentObject::StdReturn;
        }
        const auto* source = linkedMesh(Source, *this, "Source");
        Base::Vector3d normal = PlaneNormal.getValue();
        const double normalLength = normal.Length();
        if (!std::isfinite(normalLength) || normalLength <= Precision::Confusion()) {
            throw Base::ValueError("PlaneNormal must be finite and non-zero");
        }
        normal /= normalLength;
        const auto positions = PlanePositions.getValues();
        if (positions.empty()) {
            throw Base::ValueError("At least one plane position is required");
        }
        const double epsilon = Epsilon.getValue();
        if (!std::isfinite(epsilon) || epsilon < 0.0) {
            throw Base::ValueError("Epsilon must be finite and non-negative");
        }

        std::vector<Mesh::MeshObject::TPlane> planes;
        planes.reserve(positions.size());
        for (double position : positions) {
            if (!std::isfinite(position)) {
                throw Base::ValueError("Plane positions must be finite");
            }
            planes.emplace_back(
                Base::convertTo<Base::Vector3f>(normal * position),
                Base::convertTo<Base::Vector3f>(normal)
            );
        }
        Part::TopoShape result = makeSectionShape(
            *getDocument(),
            source->Mesh.getValue(),
            planes,
            epsilon,
            ConnectEdges.getValue()
        );
        if (result.isNull() || !result.isValid()) {
            throw Base::RuntimeError("The configured planes do not intersect the mesh");
        }
        Shape.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}


PROPERTY_SOURCE(MeshPart::Boundary, Part::Feature)

Boundary::Boundary()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(Source, (nullptr), "Boundary", App::Prop_None, "Linked source mesh");
    ADD_PROPERTY_TYPE(
        FacetIndices,
        (),
        "Boundary",
        App::Prop_None,
        "Optional exact source facets whose boundary is extracted"
    );
    ADD_PROPERTY_TYPE(
        AcceptedTopology,
        (Mesh::MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Source topology used to validate optional facet indices"
    );
    ADD_PROPERTY_TYPE(
        MakeFaces,
        (true),
        "Boundary",
        App::Prop_None,
        "Create faces from closed boundaries when possible"
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Boundary",
        App::Prop_None,
        "Rebuild the boundary when the linked source changes"
    );
    Source.setScope(App::LinkScope::Global);
}

bool Boundary::isSuppressed() const
{
    return operationSuppressed(suppressibleExt, *this);
}

short Boundary::mustExecute() const
{
    if (Source.isTouched() || FacetIndices.isTouched() || AcceptedTopology.isTouched()
        || MakeFaces.isTouched() || UpdateFromSource.isTouched()
        || suppressibleExt.Suppressed.isTouched()
        || sourcePropertyTouched(Source)) {
        return 1;
    }
    return Part::Feature::mustExecute();
}

App::DocumentObjectExecReturn* Boundary::execute()
{
    try {
        if (isSuppressed()) {
            if (UpdateFromSource.getValue()) {
                Shape.setValue(Part::TopoShape());
            }
            return App::DocumentObject::StdReturn;
        }
        if (!UpdateFromSource.getValue()) {
            const Part::TopoShape& cached = Shape.getShape();
            if (cached.isNull() || !cached.isValid()) {
                throw Base::RuntimeError(
                    "The detached Mesh boundary has no valid cached result"
                );
            }
            return App::DocumentObject::StdReturn;
        }
        const auto* source = linkedMesh(Source, *this, "Source");
        const Mesh::MeshObject& sourceMesh = source->Mesh.getValue();
        const auto rawIndices = FacetIndices.getValues();
        std::unique_ptr<Mesh::MeshObject> subset;
        const Mesh::MeshObject* selected = &sourceMesh;
        if (!rawIndices.empty()) {
            if (!sameMeshTopology(sourceMesh, AcceptedTopology.getValue())) {
                throw Base::RuntimeError(
                    "The linked source topology changed after the boundary facets were accepted"
                );
            }
            std::vector<Mesh::FacetIndex> indices;
            indices.reserve(rawIndices.size());
            std::vector<bool> used(sourceMesh.countFacets(), false);
            for (const long value : rawIndices) {
                if (value < 0 || static_cast<unsigned long>(value) >= sourceMesh.countFacets()) {
                    throw Base::ValueError("A boundary facet index is outside the linked mesh");
                }
                const auto index = static_cast<Mesh::FacetIndex>(value);
                if (used[index]) {
                    throw Base::ValueError("Boundary facet indices must not repeat");
                }
                used[index] = true;
                indices.push_back(index);
            }
            subset.reset(sourceMesh.meshFromSegment(indices));
            if (!subset || subset->countFacets() == 0) {
                throw Base::RuntimeError("The accepted boundary facet subset is empty");
            }
            selected = subset.get();
        }

        MeshCore::MeshKernel kernel(selected->getKernel());
        kernel.Transform(selected->getTransform());
        std::list<std::vector<Base::Vector3f>> borders;
        MeshCore::MeshAlgorithm(kernel).GetMeshBorders(borders);

        BRep_Builder builder;
        TopoDS_Compound wireCompound;
        builder.MakeCompound(wireCompound);
        std::vector<TopoDS_Wire> wires;
        for (const auto& border : borders) {
            if (border.size() < 3) {
                continue;
            }
            BRepBuilderAPI_MakePolygon polygon;
            for (auto point = border.rbegin(); point != border.rend(); ++point) {
                polygon.Add(gp_Pnt(point->x, point->y, point->z));
            }
            if ((border.front() - border.back()).Length() > Precision::Confusion()) {
                polygon.Close();
            }
            if (polygon.IsDone()) {
                const TopoDS_Wire wire = polygon.Wire();
                builder.Add(wireCompound, wire);
                wires.push_back(wire);
            }
        }
        if (wires.empty()) {
            throw Base::ValueError("The selected mesh has no open boundary");
        }

        TopoDS_Shape shape;
        if (MakeFaces.getValue()) {
            try {
                shape = Part::FaceMakerCheese::makeFace(wires);
            }
            catch (const Standard_Failure&) {
            }
        }
        if (shape.IsNull()) {
            shape = wireCompound;
        }
        Part::TopoShape result(shape, 0, getDocument()->getStringHasher());
        if (result.isNull() || !result.isValid()) {
            throw Base::RuntimeError("The mesh boundary produced invalid geometry");
        }
        Shape.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}


PROPERTY_SOURCE(MeshPart::CurveOnMesh, Part::Feature)

const char* CurveOnMesh::ContinuityEnums[] = {
    "C0",
    "C1",
    "C2",
    "C3",
    nullptr,
};

CurveOnMesh::CurveOnMesh()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(Source, (nullptr), "Projection", App::Prop_None, "Linked source mesh");
    ADD_PROPERTY_TYPE(
        AnchorFacets,
        (),
        "Projection",
        App::Prop_None,
        "Source facet index for every picked anchor"
    );
    ADD_PROPERTY_TYPE(
        AnchorWeights,
        (),
        "Projection",
        App::Prop_None,
        "Barycentric source-facet weights for every picked anchor"
    );
    ADD_PROPERTY_TYPE(
        ProjectionDirections,
        (),
        "Projection",
        App::Prop_None,
        "Source-local projection direction for every connection"
    );
    ADD_PROPERTY_TYPE(Closed, (false), "Curve", App::Prop_None, "Connect the final anchor back to the first");
    ADD_PROPERTY_TYPE(
        Approximate,
        (true),
        "Curve",
        App::Prop_None,
        "Fit smooth B-spline edges instead of retaining polylines"
    );
    ADD_PROPERTY_TYPE(MaximumDegree, (5), "Curve", App::Prop_None, "Maximum B-spline degree");
    static const App::PropertyIntegerConstraint::Constraints degreeRange = {
        1,
        25,
        1,
    };
    MaximumDegree.setConstraints(&degreeRange);
    ADD_PROPERTY_TYPE(Continuity, (2L), "Curve", App::Prop_None, "Requested B-spline continuity");
    Continuity.setEnums(ContinuityEnums);
    ADD_PROPERTY_TYPE(Tolerance, (1.0e-2), "Curve", App::Prop_None, "B-spline fitting tolerance");
    ADD_PROPERTY_TYPE(
        SplitAngle,
        (Base::toDegrees<double>(std::numbers::pi / 4.0)),
        "Curve",
        App::Prop_None,
        "Start a separate curve when adjacent anchor segments exceed this "
        "angle"
    );
    Source.setScope(App::LinkScope::Global);
}

bool CurveOnMesh::isSuppressed() const
{
    return operationSuppressed(suppressibleExt, *this);
}

short CurveOnMesh::mustExecute() const
{
    if (Source.isTouched() || AnchorFacets.isTouched() || AnchorWeights.isTouched()
        || ProjectionDirections.isTouched() || Closed.isTouched() || Approximate.isTouched()
        || MaximumDegree.isTouched() || Continuity.isTouched() || Tolerance.isTouched()
        || SplitAngle.isTouched() || suppressibleExt.Suppressed.isTouched()
        || sourcePropertyTouched(Source)) {
        return 1;
    }
    return Part::Feature::mustExecute();
}

App::DocumentObjectExecReturn* CurveOnMesh::execute()
{
    try {
        if (isSuppressed()) {
            Shape.setValue(Part::TopoShape());
            return App::DocumentObject::StdReturn;
        }
        const auto* source = linkedMesh(Source, *this, "Source");
        const auto facets = AnchorFacets.getValues();
        const auto weights = AnchorWeights.getValues();
        const auto directions = ProjectionDirections.getValues();
        if (facets.size() < 2 || facets.size() != weights.size()) {
            throw Base::ValueError("Curve-on-mesh anchors must contain matching facet indices "
                                   "and barycentric weights");
        }
        const std::size_t connectionCount = Closed.getValue() ? facets.size() : facets.size() - 1;
        if (directions.size() != connectionCount) {
            throw Base::ValueError("Curve-on-mesh projection directions do not match its "
                                   "anchor connections");
        }
        const double fitTolerance = Tolerance.getValue();
        if (!std::isfinite(fitTolerance) || fitTolerance <= Precision::Confusion()) {
            throw Base::ValueError("Tolerance must be finite and greater than the OCC "
                                   "confusion tolerance");
        }
        if (Continuity.getValue() < 0 || Continuity.getValue() > 3) {
            throw Base::ValueError("Continuity must be C0, C1, C2, or C3");
        }

        const Base::Matrix4D sourceTransform = source->Mesh.getValue().getTransform();
        MeshCore::MeshKernel kernel(source->Mesh.getValue().getKernel());
        kernel.Transform(sourceTransform);
        if (kernel.CountFacets() == 0) {
            throw Base::ValueError("Source contains no facets");
        }
        std::vector<Base::Vector3f> anchors;
        anchors.reserve(facets.size());
        for (std::size_t index = 0; index < facets.size(); ++index) {
            const long facetIndex = facets[index];
            if (facetIndex < 0 || static_cast<unsigned long>(facetIndex) >= kernel.CountFacets()) {
                throw Base::ValueError("A curve-on-mesh anchor facet no longer exists");
            }
            const Base::Vector3d weight = weights[index];
            const double sum = weight.x + weight.y + weight.z;
            if (!std::isfinite(sum) || std::abs(sum - 1.0) > 1.0e-4 || weight.x < -1.0e-5
                || weight.y < -1.0e-5 || weight.z < -1.0e-5 || weight.x > 1.00001
                || weight.y > 1.00001 || weight.z > 1.00001) {
                throw Base::ValueError("A curve-on-mesh anchor has invalid barycentric weights");
            }
            const auto triangle = kernel.GetFacet(static_cast<MeshCore::FacetIndex>(facetIndex));
            anchors.push_back(
                triangle._aclPoints[0] * static_cast<float>(weight.x)
                + triangle._aclPoints[1] * static_cast<float>(weight.y)
                + triangle._aclPoints[2] * static_cast<float>(weight.z)
            );
        }

        MeshCore::MeshAlgorithm algorithm(kernel);
        const float averageEdgeLength = algorithm.GetAverageEdgeLength();
        if (!std::isfinite(averageEdgeLength) || averageEdgeLength <= 0.0F) {
            throw Base::ValueError("Source has no usable mesh edges");
        }
        MeshCore::MeshFacetGrid grid(kernel, 5.0F * averageEdgeLength);
        MeshCore::MeshProjection projection(kernel);
        std::vector<std::vector<Base::Vector3f>> curveSegments;
        const double splitAngleRadians = Base::toRadians<double>(SplitAngle.getValue());
        if (!std::isfinite(splitAngleRadians) || splitAngleRadians < 0.0
            || splitAngleRadians > std::numbers::pi) {
            throw Base::ValueError("SplitAngle must be between zero and 180 degrees");
        }
        const double splitCosine = std::cos(splitAngleRadians);
        for (std::size_t index = 0; index < connectionCount; ++index) {
            const std::size_t next = (index + 1) % anchors.size();
            Base::Vector3d direction = transformDirection(sourceTransform, directions[index]);
            if (!std::isfinite(direction.x) || !std::isfinite(direction.y)
                || !std::isfinite(direction.z) || direction.Length() <= Precision::Confusion()) {
                throw Base::ValueError("A curve-on-mesh projection direction is invalid");
            }
            direction.Normalize();
            std::vector<Base::Vector3f> polyline;
            if (!projection.projectLineOnMesh(
                    grid,
                    anchors[index],
                    static_cast<MeshCore::FacetIndex>(facets[index]),
                    anchors[next],
                    static_cast<MeshCore::FacetIndex>(facets[next]),
                    Base::convertTo<Base::Vector3f>(direction),
                    polyline
                )
                || polyline.size() < 2) {
                throw Base::RuntimeError("A curve segment can no longer be projected across the "
                                         "linked mesh");
            }

            bool split = curveSegments.empty();
            if (!split && index > 0) {
                Base::Vector3f incoming = anchors[index] - anchors[index - 1];
                Base::Vector3f outgoing = anchors[next] - anchors[index];
                if (incoming.Length() <= Precision::Confusion()
                    || outgoing.Length() <= Precision::Confusion()) {
                    throw Base::ValueError("Curve-on-mesh anchors must be distinct");
                }
                incoming.Normalize();
                outgoing.Normalize();
                split = incoming * outgoing < splitCosine;
            }
            if (split) {
                curveSegments.push_back(std::move(polyline));
            }
            else {
                auto& current = curveSegments.back();
                current.insert(current.end(), std::next(polyline.begin()), polyline.end());
            }
        }

        BRep_Builder builder;
        TopoDS_Compound compound;
        builder.MakeCompound(compound);
        bool hasGeometry = false;
        for (const auto& points : curveSegments) {
            if (Approximate.getValue()) {
                if (points.size() < 2) {
                    continue;
                }
                TColgp_Array1OfPnt fitPoints(1, static_cast<Standard_Integer>(points.size()));
                Standard_Integer pointIndex = 1;
                for (const auto& point : points) {
                    fitPoints.SetValue(pointIndex++, gp_Pnt(point.x, point.y, point.z));
                }
                const auto continuity = continuityFromIndex(Continuity.getValue());
                GeomAPI_PointsToBSpline
                    fit(fitPoints, 1, MaximumDegree.getValue(), continuity, fitTolerance);
                const Handle(Geom_BSplineCurve) curve = fit.Curve();
                if (curve.IsNull()) {
                    throw Base::RuntimeError("B-spline approximation failed");
                }
                BRepBuilderAPI_MakeEdge edge(curve);
                if (!edge.IsDone() || edge.Edge().IsNull()) {
                    throw Base::RuntimeError("B-spline edge construction failed");
                }
                builder.Add(compound, edge.Edge());
                hasGeometry = true;
            }
            else {
                BRepBuilderAPI_MakePolygon polygon;
                for (const auto& point : points) {
                    polygon.Add(gp_Pnt(point.x, point.y, point.z));
                }
                if (!polygon.IsDone() || polygon.Wire().IsNull()) {
                    throw Base::RuntimeError("Polyline construction failed");
                }
                builder.Add(compound, polygon.Wire());
                hasGeometry = true;
            }
        }
        if (!hasGeometry) {
            throw Base::RuntimeError("Curve projection produced no geometry");
        }
        Part::TopoShape result(compound, 0, getDocument()->getStringHasher());
        if (result.isNull() || !result.isValid()) {
            throw Base::RuntimeError("Curve projection produced invalid geometry");
        }
        Shape.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        Shape.setValue(Part::TopoShape());
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}

}  // namespace MeshPart
