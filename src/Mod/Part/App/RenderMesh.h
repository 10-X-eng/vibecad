// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <cstdint>
#include <stop_token>
#include <vector>

#include <Mod/Part/PartGlobal.h>

class TopoDS_Shape;

namespace Part
{

/**
 * Renderer-neutral tessellation prepared without touching a GUI scene graph.
 *
 * Face and line index streams use -1 as the primitive delimiter expected by
 * Inventor-style indexed geometry. faceTriangleCounts retains one entry for
 * every topological face, including faces without a triangulation.
 */
struct PartExport RenderMesh
{
    // XYZ values are intentionally stored as tightly packed floats. Besides
    // keeping this artifact renderer-neutral, a GUI owner can lend these
    // immutable buffers directly to an Inventor multi-field without copying
    // every value during adoption.
    std::vector<float> vertices;
    std::vector<float> normals;
    std::vector<std::int32_t> triangleIndices;
    std::vector<std::int32_t> faceTriangleCounts;
    std::vector<std::int32_t> lineIndices;
    // Preview rendering uses one material index per edge primitive, plus the
    // historical trailing entry. Preparing it here avoids a GUI-thread scan
    // and thousands of individual Coin field updates during adoption.
    std::vector<std::int32_t> lineMaterialIndices {0};
    std::int32_t vertexStart {0};
    // Face indices (into faceTriangleCounts) belonging to each solid. Section
    // preparation keeps touching solids separate without copying mesh buffers.
    // Empty preserves the unpartitioned path for caller-supplied meshes.
    std::vector<std::vector<std::int32_t>> solidFaceIndices;

    [[nodiscard]] std::size_t vertexCount() const
    {
        return vertices.size() / 3;
    }

    [[nodiscard]] std::size_t normalCount() const
    {
        return normals.size() / 3;
    }
};

/**
 * Deep-copy and tessellate a shape into renderer-neutral arrays.
 *
 * The source shape is only read. All triangulation changes are made on the
 * private copy, allowing callers to run independent requests concurrently.
 * Arrays are in root-local coordinates: the renderer applies the root Placement,
 * while nested child locations remain part of the prepared geometry.
 * Face-buffer extraction uses the application's shared compute runtime, with
 * disjoint output ranges and one owner per edge. The call joins that work
 * before returning; interactive callers use RenderMeshController, not this
 * synchronous preparation API on the GUI owner.
 */
PartExport RenderMesh prepareRenderMesh(
    const TopoDS_Shape& shape,
    double deviation,
    double angularDeflection,
    bool normalsFromUV = false,
    std::stop_token stopToken = {}
);

}  // namespace Part
