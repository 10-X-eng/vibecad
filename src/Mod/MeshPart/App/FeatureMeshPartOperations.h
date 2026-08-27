// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/PropertyUnits.h>
#include <App/SuppressibleExtension.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/App/MeshProperties.h>
#include <Mod/Part/App/PartFeature.h>

#include <Mod/MeshPart/MeshPartGlobal.h>


namespace MeshPart
{

/**
 * Persisted result of a process-isolated shape tessellation.
 *
 * Recompute validates the prepared mesh only. BREP traversal and tessellation
 * belong to the background Mesh From Shape workflow, never document execute.
 */
class MeshPartExport MeshFromShape: public Mesh::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::MeshFromShape);

public:
    enum class MeshingMethod : long
    {
        Standard = 0,
        Mefisto,
        Netgen,
        Gmsh,
    };

    MeshFromShape();

    App::PropertyLinkSub Source;
    App::PropertyEnumeration Method;
    App::PropertyLength LinearDeflection;
    App::PropertyFloatConstraint AngularDeflection;
    App::PropertyBool Relative;
    App::PropertyBool Segments;
    App::PropertyLength MaximumEdgeLength;
    App::PropertyInteger Fineness;
    App::PropertyFloatConstraint GrowthRate;
    App::PropertyFloatConstraint SegmentsPerEdge;
    App::PropertyFloatConstraint SegmentsPerRadius;
    App::PropertyBool SecondOrder;
    App::PropertyBool Optimize;
    App::PropertyBool QuadDominated;
    App::PropertyInteger GmshAlgorithm;
    App::PropertyLength GmshMinimumSize;
    App::PropertyLength GmshMaximumSize;
    App::PropertyLength GmshGeometryTolerance;
    App::PropertyIntegerConstraint GmshElementOrder;
    App::PropertyBool GmshOptimize;
    App::PropertyString GmshExecutable;
    App::PropertyIntegerConstraint GmshTimeoutSeconds;
    App::PropertyString CachedGmshSourceBrep;
    Mesh::PropertyMeshKernel CachedGmshResult;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    static const char* MethodEnums[];
    App::SuppressibleExtension suppressibleExt;
};

/**
 * Recomputable OCC shape generated from one linked mesh.
 */
class MeshPartExport ShapeFromMesh: public Part::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::ShapeFromMesh);

public:
    ShapeFromMesh();

    App::PropertyLink Source;
    App::PropertyLength Tolerance;
    App::PropertyBool SewShape;
    App::PropertyBool MakeSolid;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    App::SuppressibleExtension suppressibleExt;
};

/**
 * Recomputable intersection of a linked mesh with a linked datum plane.
 */
class MeshPartExport SectionByPlane: public Part::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::SectionByPlane);

public:
    SectionByPlane();

    App::PropertyLink Source;
    App::PropertyLink Plane;
    App::PropertyLength MinimumLength;
    App::PropertyBool ConnectEdges;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    App::SuppressibleExtension suppressibleExt;
};

/**
 * Recomputable parallel cross-sections of one linked mesh.
 *
 * PlanePositions are signed distances along normalized PlaneNormal.
 */
class MeshPartExport CrossSections: public Part::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::CrossSections);

public:
    CrossSections();

    App::PropertyLink Source;
    App::PropertyVector PlaneNormal;
    App::PropertyFloatList PlanePositions;
    App::PropertyLength Epsilon;
    App::PropertyBool ConnectEdges;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    App::SuppressibleExtension suppressibleExt;
};

/**
 * Recomputable wire or face generated from the open boundary of a linked
 * mesh, optionally restricted to an exact accepted facet subset.
 */
class MeshPartExport Boundary: public Part::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::Boundary);

public:
    Boundary();

    App::PropertyLink Source;
    App::PropertyIntegerList FacetIndices;
    Mesh::PropertyMeshKernel AcceptedTopology;
    App::PropertyBool MakeFaces;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    App::SuppressibleExtension suppressibleExt;
};

/**
 * Recomputable curve projected across a linked mesh.
 *
 * Picked anchors are stored as facet indices plus barycentric weights. This
 * keeps every anchor attached to the corresponding source triangle while the
 * source points and placement change. A source topology change which
 * invalidates an anchor fails explicitly instead of publishing a stale curve.
 */
class MeshPartExport CurveOnMesh: public Part::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(MeshPart::CurveOnMesh);

public:
    CurveOnMesh();

    App::PropertyLink Source;
    App::PropertyIntegerList AnchorFacets;
    App::PropertyVectorList AnchorWeights;
    App::PropertyVectorList ProjectionDirections;
    App::PropertyBool Closed;
    App::PropertyBool Approximate;
    App::PropertyIntegerConstraint MaximumDegree;
    App::PropertyEnumeration Continuity;
    App::PropertyLength Tolerance;
    App::PropertyAngle SplitAngle;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    static const char* ContinuityEnums[];
    App::SuppressibleExtension suppressibleExt;
};

}  // namespace MeshPart
