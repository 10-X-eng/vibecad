// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <functional>
#include <memory>
#include <string>
#include <vector>
#include <Mod/Part/App/SectionGeometry.h>

namespace PartGui
{
struct SectionInstance
{
    TopoDS_Shape shape;
    Base::Matrix4D transform;
    std::shared_ptr<const Part::RenderMesh> mesh;
};

struct SectionFaceResult
{
    std::vector<TopoDS_Shape> faces;
    std::string error;
    std::shared_ptr<const Part::SectionDisplayGeometry> geometry;
};

/** One active calculation and one replaceable pending section request.
 * Requests, cancellation, and callback destruction belong to the GUI owner.
 * Workers retain only native geometry, transforms, and cancellation tokens.
 */
class PartGuiExport SectionFaceController
{
public:
    using Completion = std::function<void(SectionFaceResult)>;
    SectionFaceController();
    ~SectionFaceController();
    SectionFaceController(const SectionFaceController&) = delete;
    SectionFaceController& operator=(const SectionFaceController&) = delete;

    void request(std::vector<SectionInstance> instances,
                 Base::Vector3d origin, Base::Vector3d normal, Completion completion);
    void requestDisplay(std::vector<SectionInstance> instances,
                        Base::Vector3d origin, Base::Vector3d normal,
                        double spacing, Completion completion);
    void cancel();

private:
    struct State;
    std::shared_ptr<State> state;
};
}
