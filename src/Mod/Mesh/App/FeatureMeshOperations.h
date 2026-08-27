// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <App/GroupExtension.h>
#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/PropertyUnits.h>
#include <App/SuppressibleExtension.h>

#include "FeatureMeshDefects.h"
#include "MeshProperties.h"

namespace Mesh
{

/**
 * Recomputable tessellation of generic ComplexGeoData.
 *
 * This preserves the broad Mesh_FromGeometry command contract without
 * publishing an unlinked triangle snapshot. Source must provide complex
 * geometry through its Shape (or equivalent geometry) property.
 */
class MeshExport MeshFromGeometry: public Mesh::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::MeshFromGeometry);

public:
    MeshFromGeometry();

    App::PropertyLink Source;
    App::PropertyLength Tolerance;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    [[nodiscard]] bool isSuppressed() const;

    App::SuppressibleExtension suppressibleExt;
};

/**
 * Editable merge operation that combines linked source meshes in document
 * space. Source order is preserved so segment order and accepted design
 * intent remain stable across recomputes.
 */
class MeshExport Merge: public Mesh::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::Merge);

public:
    Merge();

    App::PropertyLinkList Sources;
    Mesh::PropertyMeshKernel AcceptedResult;
    App::PropertyStringList AcceptedSourceRevisions;
    App::PropertyPlacementList AcceptedSourcePlacements;
    App::PropertyBool AcceptedSourcesStale;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onDocumentRestored() override;

private:
    [[nodiscard]] bool isSuppressed() const;
    [[nodiscard]] bool detachedSourcesChanged() const;

    App::SuppressibleExtension suppressibleExt;
};

/**
 * One durable operation for a command which publishes several mesh results.
 *
 * Group contains the independently visible physical results. Sources records
 * the exact upstream meshes represented by the command. At full history this
 * controller has no geometry of its own. While the document marker is before
 * the operation it presents the active source meshes once, instead of making
 * every owned result publish a duplicate bypass copy.
 */
class MeshExport OutputGroup: public Mesh::Feature, public App::GroupExtension
{
    PROPERTY_HEADER_WITH_EXTENSIONS(Mesh::OutputGroup);

public:
    OutputGroup();

    App::PropertyLinkList Sources;
    App::PropertyString OperationKind;
    App::PropertyEnumeration InputMode;
    App::PropertyStringList ExternalInputs;
    App::PropertyString VibeCADTimelineRole;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    bool allowObject(App::DocumentObject* object) override;
    void extensionOnChanged(const App::Property* property) override;

    const char* getViewProviderName() const override
    {
        return "MeshGui::ViewProviderMeshOutputGroup";
    }

private:
    [[nodiscard]] bool isSuppressed() const;

    static const char* InputModeEnums[];
    App::SuppressibleExtension suppressibleExt;
};

/**
 * Editable smoothing operation that always derives its result from Source.
 */
class MeshExport Smoothing: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::Smoothing);

public:
    Smoothing();

    App::PropertyEnumeration Method;
    App::PropertyInteger Iterations;
    App::PropertyFloat Lambda;
    App::PropertyFloat Mu;
    App::PropertyIntegerList PointIndices;
    Mesh::PropertyMeshKernel SelectionSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    static const char* MethodEnums[];
};

/**
 * Editable mesh decimation operation that always derives its result from
 * Source.
 */
class MeshExport Decimation: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::Decimation);

public:
    Decimation();

    App::PropertyBool UseTargetFacetCount;
    App::PropertyInteger TargetFacetCount;
    App::PropertyFloat Tolerance;
    App::PropertyFloatConstraint Reduction;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
};

/**
 * Editable uniform scaling operation. Scaling changes mesh-local coordinates
 * and preserves Source placement and segment metadata.
 */
class MeshExport Scale: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::Scale);

public:
    Scale();

    App::PropertyFloatConstraint Factor;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
};

/**
 * Editable plane trim operation. Plane is a generic link so Mesh does not
 * acquire a dependency on the Part module; the linked object must expose a
 * Placement property.
 */
class MeshExport TrimByPlane: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::TrimByPlane);

public:
    TrimByPlane();

    App::PropertyLink Plane;
    App::PropertyEnumeration Side;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    static const char* SideEnums[];
};

/**
 * Replayable model-space polygon cut or trim.
 *
 * Polygon vertices are stored in document coordinates. Cut removes complete
 * facets selected by the polygon projection; Trim clips intersected facets at
 * the polygon boundary. Both operations can remove the projected inside or
 * outside region without depending on a camera or viewport.
 */
class MeshExport PolygonEdit: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::PolygonEdit);

public:
    PolygonEdit();

    App::PropertyVectorList Polygon;
    App::PropertyEnumeration Action;
    App::PropertyEnumeration Region;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    static const char* ActionEnums[];
    static const char* RegionEnums[];
};

/**
 * Replayable indexed topology edit.
 *
 * AddTriangle uses three point indices, RemoveFacets uses any number of facet
 * indices, and FillHole uses SeedFacet and Level.
 */
class MeshExport FacetEdit: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FacetEdit);

public:
    FacetEdit();

    App::PropertyEnumeration Action;
    App::PropertyIntegerList Indices;
    App::PropertyInteger SeedFacet;
    App::PropertyIntegerConstraint Level;
    Mesh::PropertyMeshKernel AcceptedSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;

private:
    static const char* ActionEnums[];
};

/**
 * Recomputable subset of exact source facets.
 *
 * Facet indices remain meaningful while source topology is unchanged. Point
 * coordinates and Placement may change freely; a topology change fails
 * explicitly instead of silently selecting different facets.
 */
class MeshExport FacetSubset: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::FacetSubset);

public:
    FacetSubset();

    App::PropertyIntegerList FacetIndices;
    Mesh::PropertyMeshKernel AcceptedTopology;
    Mesh::PropertyMeshKernel AcceptedResult;
    App::PropertyString AcceptedSourceRevision;
    App::PropertyBool AcceptedSourceStale;
    App::PropertyString SelectionKind;
    App::PropertyBool UpdateFromSource;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onDocumentRestored() override;
};

/**
 * Durable accepted result for operations that cannot be replayed from stable
 * model-space parameters (currently interactive polygon trims). The exact
 * accepted source is persisted and compared on recompute;
 * a changed source produces a hard error instead of silently showing stale
 * geometry.
 */
class MeshExport StoredEdit: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::StoredEdit);

public:
    StoredEdit();

    Mesh::PropertyMeshKernel AcceptedSource;
    Mesh::PropertyMeshKernel AcceptedResult;
    App::PropertyString EditKind;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
};

/**
 * Recomputable external Gmsh remeshing operation.
 *
 * The asynchronously accepted first result is persisted as a cache. Any
 * source or setting change reruns Gmsh during recompute; stale cached
 * triangles are never presented as current geometry.
 */
class MeshExport GmshRemesh: public Mesh::FixDefects
{
    PROPERTY_HEADER_WITH_OVERRIDE(Mesh::GmshRemesh);

public:
    GmshRemesh();

    App::PropertyInteger Algorithm;
    App::PropertyLength MinimumElementSize;
    App::PropertyLength MaximumElementSize;
    App::PropertyAngle SurfaceAngle;
    App::PropertyString Executable;
    App::PropertyIntegerConstraint TimeoutSeconds;
    Mesh::PropertyMeshKernel CachedSource;
    Mesh::PropertyMeshKernel CachedResult;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
};

}  // namespace Mesh
