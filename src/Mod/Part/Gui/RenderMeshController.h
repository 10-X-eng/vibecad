// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <functional>
#include <memory>
#include <string>

#include <TopoDS_Shape.hxx>

#include <Mod/Part/App/RenderMesh.h>
#include <Mod/Part/PartGlobal.h>

namespace PartGui
{

struct PartGuiExport RenderMeshResult
{
    std::shared_ptr<const Part::RenderMesh> mesh;
    std::string error;
};

/**
 * Latest-result-wins preparation of renderer-neutral Part meshes.
 *
 * Requests run on the process-lifetime compute pool. Completion always runs
 * on the GUI owner through the shared frame dispatcher. A target may have one
 * active request and one replaceable pending request, preventing rapid sketch
 * or task-panel changes from flooding the runtime with obsolete tessellations.
 */
class PartGuiExport RenderMeshController final
{
public:
    using Completion = std::function<void(RenderMeshResult)>;

    RenderMeshController();
    ~RenderMeshController();

    RenderMeshController(const RenderMeshController&) = delete;
    RenderMeshController& operator=(const RenderMeshController&) = delete;
    RenderMeshController(RenderMeshController&&) = delete;
    RenderMeshController& operator=(RenderMeshController&&) = delete;

    void request(
        const void* target,
        TopoDS_Shape shape,
        double deviation,
        double angularDeflection,
        bool normalsFromUV,
        Completion completion
    );
    void cancel(const void* target);
    void cancelAll();

private:
    struct State;
    std::shared_ptr<State> state;
};

}  // namespace PartGui
