// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>
#include <Mod/Mesh/App/Mesh.h>
#include <Mod/Mesh/App/Core/Grid.h>
#include <Mod/Mesh/App/MeshProperties.h>

#include <src/App/InitApplication.h>

class MeshTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }
};

// NOLINTBEGIN(cppcoreguidelines-*,readability-*)
TEST_F(MeshTest, TestDefault)
{
    MeshCore::MeshKernel kernel;
    Base::Vector3f p1 {0, 0, 0};
    Base::Vector3f p2 {0, 0, 1};
    Base::Vector3f p3 {0, 1, 0};
    kernel.AddFacet(MeshCore::MeshGeomFacet(p1, p2, p3));

    EXPECT_EQ(kernel.CountPoints(), 3);
    EXPECT_EQ(kernel.CountEdges(), 3);
    EXPECT_EQ(kernel.CountFacets(), 1);
}

TEST_F(MeshTest, MoveTransfersGeometryBuffersAndSegmentOwnership)
{
    MeshCore::MeshKernel kernel;
    kernel.AddFacet(MeshCore::MeshGeomFacet(
        Base::Vector3f {0, 0, 0}, Base::Vector3f {0, 0, 1}, Base::Vector3f {0, 1, 0}));
    const auto* kernelPoints = kernel.GetPoints().data();
    const auto* kernelFacets = kernel.GetFacets().data();
    MeshCore::MeshKernel transferred(std::move(kernel));
    EXPECT_EQ(transferred.GetPoints().data(), kernelPoints);
    EXPECT_EQ(transferred.GetFacets().data(), kernelFacets);

    Mesh::MeshObject source(transferred);
    source.addSegment(std::vector<Mesh::FacetIndex> {0});
    source.getSegment(0).setName("surface");
    Base::Matrix4D placement;
    placement.move(Base::Vector3d {10, 20, 30});
    source.setTransform(placement);
    const auto* points = source.getKernel().GetPoints().data();
    const auto* facets = source.getKernel().GetFacets().data();
    const auto* indices = source.getSegment(0).getIndices().data();
    const auto bounds = source.getBoundBox();

    Mesh::MeshObject constructed(std::move(source));
    EXPECT_EQ(constructed.getKernel().GetPoints().data(), points);
    EXPECT_EQ(constructed.getKernel().GetFacets().data(), facets);
    EXPECT_EQ(constructed.getSegment(0).getIndices().data(), indices);

    Base::Reference<Mesh::MeshObject> assigned(new Mesh::MeshObject(transferred));
    assigned->addSegment(std::vector<Mesh::FacetIndex> {0});
    *assigned = std::move(constructed);
    EXPECT_EQ(assigned->getKernel().GetPoints().data(), points);
    EXPECT_EQ(assigned->getKernel().GetFacets().data(), facets);
    EXPECT_EQ(assigned->getSegment(0).getIndices().data(), indices);
    EXPECT_EQ(assigned->getSegment(0).getName(), "surface");
    EXPECT_DOUBLE_EQ(assigned->getBoundBox().MinX, bounds.MinX);
    EXPECT_DOUBLE_EQ(assigned->getBoundBox().MaxZ, bounds.MaxZ);
    // Segment iterators must address the new owner, not either moved-from mesh.
    EXPECT_DOUBLE_EQ(assigned->getSegment(0).facets_begin()->_aclPoints[0].x, 10.0);
    *assigned = std::move(*assigned);
    EXPECT_EQ(assigned->countFacets(), 1);
}

TEST_F(MeshTest, PropertySnapshotsRemainIndependentAndPastePreservesMeshIdentity)
{
    MeshCore::MeshKernel kernel;
    kernel.AddFacet(MeshCore::MeshGeomFacet(
        Base::Vector3f {0, 0, 0}, Base::Vector3f {0, 0, 1}, Base::Vector3f {0, 1, 0}));
    Mesh::PropertyMeshKernel source;
    source.setValue(kernel);
    std::unique_ptr<App::Property> copy(source.Copy());
    const auto& snapshot = dynamic_cast<const Mesh::PropertyMeshKernel&>(*copy);
    EXPECT_NE(source.getValue().getKernel().GetPoints().data(),
              snapshot.getValue().getKernel().GetPoints().data());
    Mesh::PropertyMeshKernel destination;
    const auto* identity = destination.getValuePtr();
    destination.Paste(snapshot);
    EXPECT_EQ(destination.getValuePtr(), identity);
    EXPECT_NE(destination.getValue().getKernel().GetPoints().data(),
              snapshot.getValue().getKernel().GetPoints().data());
    source.setValue(MeshCore::MeshKernel {});
    EXPECT_EQ(snapshot.getValue().countFacets(), 1);
    EXPECT_EQ(destination.getValue().countFacets(), 1);
    destination.Paste(destination);
    EXPECT_EQ(destination.getValuePtr(), identity);
    EXPECT_EQ(destination.getValue().countFacets(), 1);
}

TEST_F(MeshTest, TestGrid1OfPlanarMesh)
{
    MeshCore::MeshKernel kernel;
    Base::Vector3f p1 {0, 0, 0};
    Base::Vector3f p2 {1, 0, 0};
    Base::Vector3f p3 {0, 1, 0};
    Base::Vector3f p4 {1, 1, 0};
    kernel.AddFacet(MeshCore::MeshGeomFacet(p1, p2, p3));
    kernel.AddFacet(MeshCore::MeshGeomFacet(p3, p2, p4));

    MeshCore::MeshFacetGrid grid(kernel, 10);
    unsigned long countX {};
    unsigned long countY {};
    unsigned long countZ {};
    grid.GetCtGrids(countX, countY, countZ);
    EXPECT_EQ(countX, 1);
    EXPECT_EQ(countY, 1);
    EXPECT_EQ(countZ, 1);
}

TEST_F(MeshTest, TestGrid2OfPlanarMesh)
{
    MeshCore::MeshKernel kernel;
    Base::Vector3f p1 {0, 0, 0};
    Base::Vector3f p2 {1, 0, 0};
    Base::Vector3f p3 {0, 1, 0};
    Base::Vector3f p4 {1, 1, 0};
    kernel.AddFacet(MeshCore::MeshGeomFacet(p1, p2, p3));
    kernel.AddFacet(MeshCore::MeshGeomFacet(p3, p2, p4));

    MeshCore::MeshFacetGrid grid(kernel);
    unsigned long countX {};
    unsigned long countY {};
    unsigned long countZ {};
    grid.GetCtGrids(countX, countY, countZ);
    EXPECT_EQ(countX, 1);
    EXPECT_EQ(countY, 1);
    EXPECT_EQ(countZ, 1);
}

TEST_F(MeshTest, TestGrid1OfAlmostPlanarMesh)
{
    MeshCore::MeshKernel kernel;
    Base::Vector3f p1 {0, 0, 0};
    Base::Vector3f p2 {1, 0, 0};
    Base::Vector3f p3 {0, 1, 0};
    Base::Vector3f p4 {1, 1, 1.0e-18F};
    kernel.AddFacet(MeshCore::MeshGeomFacet(p1, p2, p3));
    kernel.AddFacet(MeshCore::MeshGeomFacet(p3, p2, p4));

    MeshCore::MeshFacetGrid grid(kernel, 10);
    unsigned long countX {};
    unsigned long countY {};
    unsigned long countZ {};
    grid.GetCtGrids(countX, countY, countZ);
    EXPECT_EQ(countX, 1);
    EXPECT_EQ(countY, 1);
    EXPECT_EQ(countZ, 1);
}

TEST_F(MeshTest, TestGrid2OfAlmostPlanarMesh)
{
    MeshCore::MeshKernel kernel;
    Base::Vector3f p1 {0, 0, 0};
    Base::Vector3f p2 {1, 0, 0};
    Base::Vector3f p3 {0, 1, 0};
    Base::Vector3f p4 {1, 1, 1.0e-18F};
    kernel.AddFacet(MeshCore::MeshGeomFacet(p1, p2, p3));
    kernel.AddFacet(MeshCore::MeshGeomFacet(p3, p2, p4));

    MeshCore::MeshFacetGrid grid(kernel);
    unsigned long countX {};
    unsigned long countY {};
    unsigned long countZ {};
    grid.GetCtGrids(countX, countY, countZ);
    EXPECT_EQ(countX, 1);
    EXPECT_EQ(countY, 1);
    EXPECT_EQ(countZ, 1);
}
// NOLINTEND(cppcoreguidelines-*,readability-*)
