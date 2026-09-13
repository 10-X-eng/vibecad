// SPDX-License-Identifier: LGPL-2.1-or-later
#include <Gui/AsyncImageExport.h>
#include <gtest/gtest.h>
#include <QFile>
#include <QTemporaryDir>
#include <chrono>
#include <thread>

namespace
{
bool awaitImage(Gui::detail::AsyncImageExport& job)
{
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!job.finish() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::yield();
    }
    return job.finish();
}
}

TEST(AsyncImageExport, DetachedPixelsAreWrittenAndScaledOnRuntime)
{
    QTemporaryDir directory;
    ASSERT_TRUE(directory.isValid());
    App::HostRuntime runtime(4);
    QImage source(40, 20, QImage::Format_ARGB32);
    source.fill(Qt::red);
    const auto path = directory.filePath("frame.png");
    Gui::detail::AsyncImageExport job(runtime, source, path, 20, 10, false);
    source.fill(Qt::blue);
    ASSERT_TRUE(awaitImage(job));
    EXPECT_TRUE(job.finish());
    const QImage saved(path);
    ASSERT_FALSE(saved.isNull());
    EXPECT_EQ(saved.size(), QSize(20, 10));
    EXPECT_EQ(saved.pixelColor(0, 0), QColor(Qt::red));
}

TEST(AsyncImageExport, CancellationBeforeAdmissionPreservesExistingDestination)
{
    QTemporaryDir directory;
    App::HostRuntime runtime(1);
    std::promise<void> release;
    auto ready = release.get_future().share();
    auto occupied = runtime.submit([ready](std::stop_token) { ready.wait(); });
    const auto path = directory.filePath("frame.png");
    QFile original(path);
    ASSERT_TRUE(original.open(QIODevice::WriteOnly));
    original.write("original");
    original.close();
    QImage source(40, 20, QImage::Format_ARGB32);
    source.fill(Qt::red);
    Gui::detail::AsyncImageExport job(runtime, source, path, 20, 10, false);
    job.cancel();
    EXPECT_FALSE(job.finish());
    release.set_value();
    occupied.get();
    EXPECT_THROW(awaitImage(job), std::runtime_error);
    ASSERT_TRUE(original.open(QIODevice::ReadOnly));
    EXPECT_EQ(original.readAll(), QByteArray("original"));
}

TEST(AsyncImageExport, InvalidDestinationIsReportedInsteadOfPublishingSuccess)
{
    QTemporaryDir directory;
    App::HostRuntime runtime(2);
    QImage source(2, 2, QImage::Format_ARGB32);
    source.fill(Qt::red);
    Gui::detail::AsyncImageExport job(runtime, source,
        directory.filePath("missing/frame.png"), 2, 2, false);
    EXPECT_THROW(awaitImage(job), std::runtime_error);
    EXPECT_THROW(job.finish(), std::runtime_error);
}
