// SPDX-License-Identifier: LGPL-2.1-or-later

#include <atomic>
#include <functional>
#include <thread>

#include <boost/scope_exit.hpp>
#include <gtest/gtest.h>

#include <App/MainThreadSignal.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <src/App/InitApplication.h>

namespace
{
std::atomic_bool ownerDispatchUsed {false};
std::atomic_bool blockingDispatchUsed {false};

bool reportWorkerThread()
{
    return false;
}

void invokeOwner(std::function<void()>&& function, bool blocking)
{
    ownerDispatchUsed.store(true, std::memory_order_release);
    blockingDispatchUsed.store(blocking, std::memory_order_release);
    function();
}
}  // namespace

class DrawViewThreadingTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }
};

TEST_F(DrawViewThreadingTest, PaintNotificationUsesOwnerDispatchFromNativeWorker)
{
    ownerDispatchUsed.store(false, std::memory_order_release);
    blockingDispatchUsed.store(false, std::memory_order_release);
    App::MainThreadSignalConfig::setHooks(&reportWorkerThread, &invokeOwner);
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::MainThreadSignalConfig::setHooks(nullptr, nullptr);
    };

    TechDraw::DrawView view;
    std::atomic_bool slotCalled {false};
    auto connection = view.signalGuiPaint.connect([&](const TechDraw::DrawView* source) {
        EXPECT_EQ(source, &view);
        slotCalled.store(true, std::memory_order_release);
    });

    std::thread worker([&] { view.signalGuiPaint(&view); });
    worker.join();

    EXPECT_TRUE(ownerDispatchUsed.load(std::memory_order_acquire));
    EXPECT_TRUE(blockingDispatchUsed.load(std::memory_order_acquire));
    EXPECT_TRUE(slotCalled.load(std::memory_order_acquire));
    EXPECT_TRUE(connection.connected());
}

TEST_F(DrawViewThreadingTest, PagePaintNotificationUsesOwnerDispatchFromNativeWorker)
{
    ownerDispatchUsed.store(false, std::memory_order_release);
    blockingDispatchUsed.store(false, std::memory_order_release);
    App::MainThreadSignalConfig::setHooks(&reportWorkerThread, &invokeOwner);
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::MainThreadSignalConfig::setHooks(nullptr, nullptr);
    };

    TechDraw::DrawPage page;
    std::atomic_bool slotCalled {false};
    auto connection = page.signalGuiPaint.connect([&](const TechDraw::DrawPage* source) {
        EXPECT_EQ(source, &page);
        slotCalled.store(true, std::memory_order_release);
    });

    std::thread worker([&] { page.signalGuiPaint(&page); });
    worker.join();

    EXPECT_TRUE(ownerDispatchUsed.load(std::memory_order_acquire));
    EXPECT_TRUE(blockingDispatchUsed.load(std::memory_order_acquire));
    EXPECT_TRUE(slotCalled.load(std::memory_order_acquire));
    EXPECT_TRUE(connection.connected());
}
