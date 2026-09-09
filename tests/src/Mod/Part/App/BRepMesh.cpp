// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <stdexcept>
#include <numbers>

#include <gtest/gtest.h>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRep_Builder.hxx>
#include <TopoDS_Compound.hxx>
#include <App/Application.h>
#include <App/HostRuntime.h>
#include <src/App/InitApplication.h>
#include "Mod/Part/App/BRepMesh.h"
#include "Mod/Part/App/RenderMesh.h"
#include "Mod/Part/App/ProgressIndicator.h"
#include <Message_ProgressScope.hxx>

// NOLINTBEGIN
class BRepMeshTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {}

    void TearDown() override
    {}

    std::vector<Part::BRepMesh::Domain> getNoDomains() const
    {
        std::vector<Part::BRepMesh::Domain> domains;
        return domains;
    }

    std::vector<Part::BRepMesh::Domain> getEmptyDomains() const
    {
        Part::BRepMesh::Domain domain;
        std::vector<Part::BRepMesh::Domain> domains;
        domains.push_back(domain);
        domains.push_back(domain);
        return domains;
    }

    std::vector<Part::BRepMesh::Domain> getConnectedDomains() const
    {
        Part::BRepMesh::Domain domain1;
        domain1.points.emplace_back(0, 0, 0);
        domain1.points.emplace_back(10, 0, 0);
        domain1.points.emplace_back(10, 10, 0);
        domain1.points.emplace_back(0, 10, 0);

        {
            Part::BRepMesh::Facet f1;
            f1.I1 = 0;
            f1.I2 = 1;
            f1.I3 = 2;
            domain1.facets.emplace_back(f1);
        }
        {
            Part::BRepMesh::Facet f2;
            f2.I1 = 0;
            f2.I2 = 2;
            f2.I3 = 3;
            domain1.facets.emplace_back(f2);
        }

        Part::BRepMesh::Domain domain2;
        domain2.points.emplace_back(0, 0, 0);
        domain2.points.emplace_back(0, 10, 0);
        domain2.points.emplace_back(0, 10, 10);
        domain2.points.emplace_back(0, 0, 10);

        {
            Part::BRepMesh::Facet f1;
            f1.I1 = 0;
            f1.I2 = 1;
            f1.I3 = 2;
            domain2.facets.emplace_back(f1);
        }
        {
            Part::BRepMesh::Facet f2;
            f2.I1 = 0;
            f2.I2 = 2;
            f2.I3 = 3;
            domain2.facets.emplace_back(f2);
        }

        std::vector<Part::BRepMesh::Domain> domains;
        domains.push_back(domain1);
        domains.push_back(domain2);
        return domains;
    }

    std::vector<Part::BRepMesh::Domain> getUnconnectedDomains() const
    {
        double eps = 1.0e-10;
        Part::BRepMesh::Domain domain1;
        domain1.points.emplace_back(eps, eps, eps);
        domain1.points.emplace_back(10, 0, 0);
        domain1.points.emplace_back(10, 10, 0);
        domain1.points.emplace_back(eps, 10, eps);

        {
            Part::BRepMesh::Facet f1;
            f1.I1 = 0;
            f1.I2 = 1;
            f1.I3 = 2;
            domain1.facets.emplace_back(f1);
        }
        {
            Part::BRepMesh::Facet f2;
            f2.I1 = 0;
            f2.I2 = 2;
            f2.I3 = 3;
            domain1.facets.emplace_back(f2);
        }

        Part::BRepMesh::Domain domain2;
        domain2.points.emplace_back(0, 0, 0);
        domain2.points.emplace_back(0, 10, 0);
        domain2.points.emplace_back(0, 10, 10);
        domain2.points.emplace_back(0, 0, 10);

        {
            Part::BRepMesh::Facet f1;
            f1.I1 = 0;
            f1.I2 = 1;
            f1.I3 = 2;
            domain2.facets.emplace_back(f1);
        }
        {
            Part::BRepMesh::Facet f2;
            f2.I1 = 0;
            f2.I2 = 2;
            f2.I3 = 3;
            domain2.facets.emplace_back(f2);
        }

        std::vector<Part::BRepMesh::Domain> domains;
        domains.push_back(domain1);
        domains.push_back(domain2);
        return domains;
    }
};

TEST_F(BRepMeshTest, testNoDomains)
{
    std::vector<Base::Vector3d> points;
    std::vector<Part::BRepMesh::Facet> faces;
    Part::BRepMesh brepMesh;
    brepMesh.getFacesFromDomains(getNoDomains(), points, faces);

    EXPECT_TRUE(points.empty());
    EXPECT_TRUE(faces.empty());
}

TEST_F(BRepMeshTest, testEmptyDomains)
{
    std::vector<Base::Vector3d> points;
    std::vector<Part::BRepMesh::Facet> faces;
    Part::BRepMesh brepMesh;
    brepMesh.getFacesFromDomains(getEmptyDomains(), points, faces);

    EXPECT_TRUE(points.empty());
    EXPECT_TRUE(faces.empty());
}

TEST_F(BRepMeshTest, testConnectedDomains)
{
    std::vector<Base::Vector3d> points;
    std::vector<Part::BRepMesh::Facet> faces;
    Part::BRepMesh brepMesh;
    brepMesh.getFacesFromDomains(getConnectedDomains(), points, faces);

    EXPECT_EQ(points.size(), 6);
    EXPECT_EQ(faces.size(), 4);
}

TEST_F(BRepMeshTest, testUnconnectedDomains)
{
    std::vector<Base::Vector3d> points;
    std::vector<Part::BRepMesh::Facet> faces;
    Part::BRepMesh brepMesh;
    brepMesh.getFacesFromDomains(getUnconnectedDomains(), points, faces);

    EXPECT_EQ(points.size(), 6);
    EXPECT_EQ(faces.size(), 4);
}

TEST_F(BRepMeshTest, preparesCompleteRenderArraysWithoutCoinNodes)
{
    const TopoDS_Shape box = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape();

    const Part::RenderMesh mesh = Part::prepareRenderMesh(box, 0.5, 28.5, true);

    EXPECT_FALSE(mesh.vertices.empty());
    EXPECT_EQ(mesh.normalCount(), mesh.vertexStart);
    EXPECT_EQ(mesh.triangleIndices.size() % 4, 0);
    EXPECT_EQ(mesh.faceTriangleCounts.size(), 6);
    EXPECT_FALSE(mesh.lineIndices.empty());
    EXPECT_EQ(
        mesh.lineMaterialIndices.size(),
        static_cast<std::size_t>(std::count(mesh.lineIndices.begin(), mesh.lineIndices.end(), -1))
            + 1
    );
    EXPECT_TRUE(std::all_of(
        mesh.lineMaterialIndices.begin(),
        mesh.lineMaterialIndices.end(),
        [](std::int32_t material) {
            return material == 0;
        }
    ));
    EXPECT_EQ(mesh.vertexStart + 8, mesh.vertexCount());
}

TEST_F(BRepMeshTest, preparesFiftyLocatedInstancesOnTheSharedRuntime)
{
    BRep_Builder builder;
    TopoDS_Compound assembly;
    builder.MakeCompound(assembly);
    const TopoDS_Shape box = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape();
    for (int index = 0; index < 50; ++index) {
        gp_Trsf placement;
        placement.SetRotation(gp_Ax1(gp_Pnt(), gp_Dir(0, 0, 1)),
                              index % 2 ? std::numbers::pi / 2 : 0);
        placement.SetTranslationPart(gp_Vec(index * 40.0, 0.0, 0.0));
        builder.Add(assembly, box.Located(TopLoc_Location(placement)));
    }
    auto& runtime = App::GetApplication().hostRuntime();
    std::vector<std::future<Part::RenderMesh>> jobs;
    for (const bool useUV : {false, true}) {
        jobs.push_back(runtime.submit([assembly, useUV](std::stop_token stop) {
            return Part::prepareRenderMesh(assembly, 0.5, 28.5, useUV, stop);
        }));
    }
    for (auto& job : jobs) {
        const auto mesh = job.get();
        EXPECT_EQ(mesh.faceTriangleCounts.size(), 300);
        EXPECT_EQ(mesh.triangleIndices.size(), 50 * 12 * 4);
        EXPECT_EQ(std::count(mesh.lineIndices.begin(), mesh.lineIndices.end(), -1), 600);
        EXPECT_EQ(mesh.lineMaterialIndices.size(), 601);
        EXPECT_EQ(mesh.vertexStart + 400, mesh.vertexCount());
        for (const auto index : mesh.triangleIndices) {
            EXPECT_TRUE(index == -1 || (index >= 0 && index < mesh.vertexStart));
        }
        for (const auto index : mesh.lineIndices) {
            EXPECT_TRUE(index == -1 || (index >= 0 && index < mesh.vertexCount()));
        }
        for (std::size_t offset = 0; offset < mesh.triangleIndices.size(); offset += 4) {
            const auto point = [&](int vertex) {
                return Base::Vector3f(mesh.vertices[vertex * 3],
                                      mesh.vertices[vertex * 3 + 1],
                                      mesh.vertices[vertex * 3 + 2]);
            };
            const int first = mesh.triangleIndices[offset];
            auto geometric = (point(mesh.triangleIndices[offset + 1]) - point(first))
                % (point(mesh.triangleIndices[offset + 2]) - point(first));
            geometric.Normalize();
            const Base::Vector3f normal(mesh.normals[first * 3],
                                        mesh.normals[first * 3 + 1],
                                        mesh.normals[first * 3 + 2]);
            EXPECT_GT(geometric * normal, 0.99f);
        }
        for (std::size_t offset = 0; offset < mesh.normals.size(); offset += 3) {
            const auto x = mesh.normals[offset];
            const auto y = mesh.normals[offset + 1];
            const auto z = mesh.normals[offset + 2];
            EXPECT_NEAR(x * x + y * y + z * z, 1.0, 1e-5);
        }
        float maximumX = 0;
        for (std::size_t offset = 0; offset < mesh.vertices.size(); offset += 3) {
            maximumX = std::max(maximumX, mesh.vertices[offset]);
        }
        EXPECT_FLOAT_EQ(maximumX, 49 * 40.0f);
    }
}

TEST_F(BRepMeshTest, renderCoordinatesExcludeOnlyTheRootPlacement)
{
    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);
    gp_Trsf childPlacement;
    childPlacement.SetTranslation(gp_Vec(40, 10, 5));
    const auto child = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
        .Located(TopLoc_Location(childPlacement));
    builder.Add(compound, child);
    gp_Trsf placement;
    placement.SetRotation(gp_Ax1(gp_Pnt(), gp_Dir(0, 0, 1)), std::numbers::pi / 2);
    placement.SetTranslationPart(gp_Vec(100, 200, 300));
    const auto located = compound.Located(TopLoc_Location(placement));
    const auto originalRootLocation = located.Location();
    const auto originalChildLocation = child.Location();
    const auto localMesh = Part::prepareRenderMesh(compound, 0.5, 28.5, true);
    const auto placedMesh = Part::prepareRenderMesh(located, 0.5, 28.5, true);
    // The scene graph applies the root Placement exactly once; nested compound
    // placements remain baked into the private render coordinates.
    EXPECT_EQ(placedMesh.vertices, localMesh.vertices);
    EXPECT_EQ(placedMesh.normals, localMesh.normals);
    EXPECT_EQ(placedMesh.triangleIndices, localMesh.triangleIndices);
    EXPECT_EQ(placedMesh.lineIndices, localMesh.lineIndices);
    ASSERT_FALSE(localMesh.vertices.empty());
    EXPECT_FLOAT_EQ(localMesh.vertices[0], 40);
    EXPECT_TRUE(located.Location().IsEqual(originalRootLocation));
    EXPECT_TRUE(child.Location().IsEqual(originalChildLocation));
    EXPECT_TRUE(compound.Location().IsIdentity());
}

TEST_F(BRepMeshTest, kernelProgressObservesWorkerCancellation)
{
    std::stop_source cancellation;
    Handle(Part::ProgressIndicator) indicator =
        new Part::ProgressIndicator(cancellation.get_token());
    Message_ProgressScope scope(indicator->Start(), "Meshing", 50);
    EXPECT_FALSE(scope.UserBreak());
    {
        Message_ProgressScope face(scope.Next(), "Face", 3);
        EXPECT_FALSE(face.UserBreak());
        cancellation.request_stop();
        EXPECT_TRUE(face.UserBreak());
    }
    EXPECT_TRUE(scope.UserBreak());
}

TEST_F(BRepMeshTest, cancelledRenderCannotReturnAPartialArtifact)
{
    std::stop_source cancellation;
    cancellation.request_stop();
    const TopoDS_Shape box = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape();
    EXPECT_THROW(
        Part::prepareRenderMesh(box, 0.5, 28.5, true, cancellation.get_token()),
        std::runtime_error);
    EXPECT_THROW(
        Part::prepareRenderMesh(TopoDS_Shape(), 0.5, 28.5, true, cancellation.get_token()),
        std::runtime_error);
}
// NOLINTEND
