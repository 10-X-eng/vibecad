// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <App/HostRuntime.h>
#include <QImage>
#include <QSaveFile>
#include <chrono>
#include <exception>
#include <future>
#include <optional>
#include <stop_token>

namespace Gui::detail
{
/// Detached image processing; no QWidget, viewer or scene-graph references.
/// The owner polls completion, never waits. Destruction requests cancellation.
class AsyncImageExport final
{
public:
    AsyncImageExport(App::HostRuntime& runtime, QImage image, QString path,
                     int width, int height, bool flipVertically)
    {
        if (image.isNull() || path.isEmpty() || width <= 0 || height <= 0) {
            throw std::invalid_argument("Frame export requires pixels, a destination and positive dimensions");
        }
        future = runtime.submit(
            [image = std::move(image), path = std::move(path), width, height,
             flipVertically, cancellation = stop.get_token()](std::stop_token shutdown) mutable {
                const auto check = [&] {
                    Base::CancellationScope::check();
                    if (cancellation.stop_requested() || shutdown.stop_requested()) {
                        throw std::runtime_error("Frame export cancelled");
                    }
                };
                check();
                if (flipVertically) {
#if QT_VERSION < QT_VERSION_CHECK(6, 9, 0)
                    image = image.mirrored();
#else
                    image = image.flipped(Qt::Vertical);
#endif
                }
                if (image.width() != width || image.height() != height) {
                    image = image.scaled(width, height, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
                }
                check();
                QSaveFile destination(path);
                destination.setDirectWriteFallback(false);
                if (!destination.open(QIODevice::WriteOnly)) {
                    throw std::runtime_error(destination.errorString().toStdString());
                }
                if (!image.save(&destination, "PNG")) {
                    throw std::runtime_error("Could not encode the captured frame as PNG");
                }
                check();
                if (!destination.commit()) {
                    throw std::runtime_error(destination.errorString().toStdString());
                }
            });
    }

    ~AsyncImageExport() { cancel(); }
    AsyncImageExport(const AsyncImageExport&) = delete;
    AsyncImageExport& operator=(const AsyncImageExport&) = delete;

    void cancel() { stop.request_stop(); }

    bool isReady() const
    {
        return completion.has_value()
            || future.wait_for(std::chrono::seconds(0)) == std::future_status::ready;
    }

    bool finish()
    {
        if (!isReady()) {
            return false;
        }
        if (!completion.has_value()) {
            try {
                future.get();
                completion = std::exception_ptr {};
            }
            catch (...) {
                completion = std::current_exception();
            }
        }
        if (*completion) {
            std::rethrow_exception(*completion);
        }
        return true;
    }

private:
    std::stop_source stop;
    std::future<void> future;
    std::optional<std::exception_ptr> completion;
};
}
