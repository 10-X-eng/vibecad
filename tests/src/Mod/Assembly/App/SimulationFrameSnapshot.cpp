// SPDX-License-Identifier: LGPL-2.1-or-later
#include <gtest/gtest.h>
#include <Mod/Assembly/App/SimulationFrameSnapshot.h>
#include <App/HostRuntime.h>
#include <OndselSolver/ASMTPart.h>

TEST(SimulationFrameSnapshot, MatchesSolverAndOwnsItsSamples)
{
    MbD::ASMTPart part;
    part.xs = std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{1, 2, 3});
    part.ys = part.xs->copy();
    part.zs = part.xs->copy();
    part.bryxs = std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{0.3, -0.7, 1.1});
    part.bryys = std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{-0.1, 0.4, -0.9});
    part.bryzs = std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{2.1, 1.5, -2.7});
    Base::Placement offset(Base::Vector3d(3, 4, 5), Base::Rotation(0.2, 0.3, 0.4, 0.5));
    const auto track = Assembly::detail::SimulationFrameTrack::capture(part, offset, 3);
    for (size_t frame = 0; frame < 3; ++frame) {
        part.setPosition3D(part.getPosition3D(frame));
        part.setRotationMatrix(part.getRotationMatrix(frame));
        double x, y, z, w, qx, qy, qz;
        part.getPosition3D(x, y, z);
        part.getQuarternions(w, qx, qy, qz);
        const auto expected = Base::Placement(Base::Vector3d(x, y, z), Base::Rotation(qx, qy, qz, w)) * offset;
        EXPECT_TRUE(track.placementAt(frame).isSame(expected, 1e-12));
    }
    const auto expected = track.placementAt(1);
    part.xs->at(1) = 100;
    part.bryxs->at(1) = 100;
    EXPECT_TRUE(track.placementAt(1).isSame(expected, 1e-12));
    EXPECT_THROW(track.placementAt(3), std::out_of_range);
    part.ys->clear();
    EXPECT_THROW(Assembly::detail::SimulationFrameTrack::capture(part, offset, 3), std::runtime_error);
}

TEST(SimulationFrameSnapshot, IndependentComponentsUseTheSharedExecutor)
{
    MbD::ASMTPart part;
    part.xs = part.ys = part.zs = part.bryxs = part.bryys = part.bryzs =
        std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{0.1, 0.2});
    const auto track = Assembly::detail::SimulationFrameTrack::capture(part, {}, 2);
    App::HostRuntime runtime(4);
    std::vector<Base::Placement> results(500);
    auto completion = runtime.submit([&](std::stop_token) {
        runtime.parallelFor(results.size(), [&](size_t index) {
            results[index] = track.placementAt(index % 2);
        });
    });
    runtime.wait(completion);
    for (size_t index = 0; index < results.size(); ++index) {
        EXPECT_TRUE(results[index].isSame(track.placementAt(index % 2), 1e-12));
    }
}
