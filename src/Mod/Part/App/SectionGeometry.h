// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <stop_token>
#include <array>
#include <vector>
#include <TopoDS_Shape.hxx>
#include <Base/Matrix.h>
#include <Base/Vector3D.h>
#include <Mod/Part/PartGlobal.h>

namespace Part
{
struct RenderMesh;

/** Prepare visual section faces from immutable, already displayed triangles.
 * Curved boundaries follow display tessellation; exact CAD callers retain
 * prepareSectionFaces. No source buffers or document geometry are modified.
 */
PartExport TopoDS_Shape prepareSectionMeshFaces(
    const RenderMesh& mesh, const Base::Matrix4D& displayedTransform,
    const Base::Vector3d& origin, const Base::Vector3d& normal,
    std::stop_token stopToken = {}
);

/** Prepare exact section faces from a cached rendered shape on a compute worker.
 * The root location is replaced by the displayed instance transform, matching
 * render-mesh placement. Source geometry is never mutated. No GUI/Python access.
 */
PartExport TopoDS_Shape prepareSectionFaces(
    const TopoDS_Shape& source,
    const Base::Matrix4D& displayedTransform,
    const Base::Vector3d& origin,
    const Base::Vector3d& normal,
    std::stop_token stopToken = {}
);

struct SectionDisplayGeometry
{
    std::vector<std::array<Base::Vector3d, 3>> triangles;
    std::vector<std::array<Base::Vector3d, 2>> hatch;
    std::vector<std::array<Base::Vector3d, 2>> outlines;
};

/** Prepare renderer-neutral cap triangles and lines on a compute worker. */
PartExport SectionDisplayGeometry prepareSectionDisplay(
    const TopoDS_Shape& faces, const Base::Vector3d& origin,
    const Base::Vector3d& normal, double spacing, std::stop_token stopToken = {}
);
}
