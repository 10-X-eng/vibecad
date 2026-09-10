// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <stop_token>
#include <TopoDS_Shape.hxx>
#include <Base/Matrix.h>
#include <Base/Vector3D.h>
#include <Mod/Part/PartGlobal.h>

namespace Part
{
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
}
