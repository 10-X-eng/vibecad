// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <array>
#include <cmath>
#include <stdexcept>
#include <vector>
#include <Base/Placement.h>
#include <OndselSolver/ASMTSpatialItem.h>

namespace Assembly::detail
{
// Value-only kinematic channels. Capture while the detached solver is exclusively
// owned; afterwards the solver can change or disappear without affecting readers.
// Store the six solved channels, not an eagerly expanded matrix for every frame.
class SimulationFrameTrack
{
public:
    size_t frameCount() const noexcept { return channels[0].size(); }

    static SimulationFrameTrack capture(
        const MbD::ASMTSpatialItem& part, const Base::Placement& offset, size_t frames)
    {
        SimulationFrameTrack result;
        result.offset = offset;
        const std::array<MbD::FRowDsptr, 6> source {
            part.xs, part.ys, part.zs, part.bryxs, part.bryys, part.bryzs};
        for (size_t channel = 0; channel < source.size(); ++channel) {
            if (!source[channel] || source[channel]->size() != frames) {
                throw std::runtime_error("Simulation component has incomplete frame channels");
            }
            result.channels[channel].assign(source[channel]->begin(), source[channel]->end());
            for (double value : result.channels[channel]) {
                if (!std::isfinite(value)) {
                    throw std::runtime_error("Simulation component has a non-finite frame channel");
                }
            }
        }
        return result;
    }

    Base::Placement placementAt(size_t frame) const
    {
        // Ondsel stores Bryant angles in Rx * Ry * Rz order. Compose the same
        // rotation directly, without allocating three matrices per component.
        const double x = channels[3].at(frame) * 0.5;
        const double y = channels[4].at(frame) * 0.5;
        const double z = channels[5].at(frame) * 0.5;
        const auto rotation = Base::Rotation(std::sin(x), 0, 0, std::cos(x))
            * Base::Rotation(0, std::sin(y), 0, std::cos(y))
            * Base::Rotation(0, 0, std::sin(z), std::cos(z));
        return Base::Placement(
            Base::Vector3d(channels[0].at(frame), channels[1].at(frame), channels[2].at(frame)),
            rotation) * offset;
    }

private:
    friend class SimulationPlaybackCache;
    std::array<std::vector<double>, 6> channels;
    Base::Placement offset;
};
}
