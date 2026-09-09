// SPDX-License-Identifier: LGPL-2.1-or-later
#include <gtest/gtest.h>
#include <QFile>
#include <QTemporaryDir>
#include <Mod/Assembly/App/SimulationPlaybackCache.h>
#include <OndselSolver/ASMTPart.h>
#include <OndselSolver/ASMTAssembly.h>

TEST(SimulationPlaybackCache, AuthenticatesCompleteTracksAndRejectsCorruption)
{
    QTemporaryDir directory;
    ASSERT_TRUE(directory.isValid());
    Assembly::detail::SimulationPlaybackCache cache(directory.path().toStdString());
    MbD::ASMTPart part;
    part.xs = part.ys = part.zs = part.bryxs = part.bryys = part.bryzs =
        std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{0.1, 0.2, 0.3});
    const Base::Placement offset(Base::Vector3d(2, 3, 4), Base::Rotation());
    const auto track = Assembly::detail::SimulationFrameTrack::capture(part, offset, 3);
    const std::string key(64, 'a');
    EXPECT_FALSE(cache.load(key, {offset}));
    cache.store(key, {track});
    const auto loaded = cache.load(key, {offset});
    ASSERT_TRUE(loaded);
    ASSERT_EQ(loaded->size(), 1);
    for (size_t frame = 0; frame < 3; ++frame) {
        EXPECT_TRUE(loaded->front().placementAt(frame).isSame(track.placementAt(frame), 1e-12));
    }
    EXPECT_FALSE(cache.load(std::string(64, 'b'), {offset}));
    EXPECT_FALSE(cache.load(key, {offset, offset}));
    QFile file(directory.filePath(QString::fromStdString(key) + ".frames"));
    ASSERT_TRUE(file.open(QIODevice::ReadWrite));
    ASSERT_TRUE(file.seek(file.size() - 1));
    const auto last = file.read(1);
    ASSERT_TRUE(file.seek(file.size() - 1));
    ASSERT_EQ(file.write(QByteArray(1, last.front() ^ 1)), 1);
    file.close();
    EXPECT_FALSE(cache.load(key, {offset}));
    // A valid completed replacement remains reusable after a bad cache entry.
    cache.store(key, {track});
    ASSERT_TRUE(cache.load(key, {offset}));
}

TEST(SimulationPlaybackCache, RejectsTruncatedAndMisidentifiedData)
{
    QTemporaryDir directory;
    Assembly::detail::SimulationPlaybackCache cache(directory.path().toStdString());
    MbD::ASMTPart part;
    part.xs = part.ys = part.zs = part.bryxs = part.bryys = part.bryzs =
        std::make_shared<MbD::FullRow<double>>(std::initializer_list<double>{0, 1});
    const auto track = Assembly::detail::SimulationFrameTrack::capture(part, {}, 2);
    const std::string key(64, 'c');
    cache.store(key, {track});
    QFile file(directory.filePath(QString::fromStdString(key) + ".frames"));
    ASSERT_TRUE(file.open(QIODevice::ReadWrite));
    ASSERT_TRUE(file.resize(file.size() - 1));
    file.close();
    EXPECT_FALSE(cache.load(key, {{}}));
    EXPECT_THROW(cache.load("../invalid", {{}}), std::invalid_argument);
}

TEST(SimulationPlaybackCache, InputKeyIsExactAndIndependentOfContainerOrder)
{
    auto assembly = MbD::ASMTAssembly::With();
    auto first = MbD::ASMTPart::With();
    auto second = MbD::ASMTPart::With();
    first->setName("first");
    second->setName("second");
    assembly->addPart(first);
    assembly->addPart(second);
    using Cache = Assembly::detail::SimulationPlaybackCache;
    const std::vector<Cache::Binding> bindings {{"first", {}}, {"second", {}}};
    const auto original = Cache::inputKey(*assembly, bindings, "producer-1");
    std::reverse(assembly->parts->begin(), assembly->parts->end());
    EXPECT_EQ(original, Cache::inputKey(*assembly, bindings, "producer-1"));
    EXPECT_NE(original, Cache::inputKey(*assembly, bindings, "producer-2"));
    auto changed = bindings;
    changed[0].second.setPosition(Base::Vector3d(0, 0, 1e-14));
    EXPECT_NE(original, Cache::inputKey(*assembly, changed, "producer-1"));
    first->setPosition3D(0, 0, 1e-14);
    EXPECT_NE(original, Cache::inputKey(*assembly, bindings, "producer-1"));
}
