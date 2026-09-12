// SPDX-License-Identifier: LGPL-2.1-or-later

#include "RenderMeshController.h"

#include <chrono>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <stop_token>
#include <unordered_map>
#include <utility>

#include <QApplication>
#include <QThread>

#include <Standard_Failure.hxx>

#include <App/Application.h>
#include <App/HostRuntime.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/FrameBudget.h>

using namespace PartGui;

namespace
{

void requireGuiOwner()
{
    if (!qApp || QThread::currentThread() != qApp->thread()) {
        throw std::logic_error("Render mesh requests must originate on the GUI owner thread");
    }
}

}  // namespace

struct RenderMeshController::State: std::enable_shared_from_this<State>
{
    struct Request
    {
        std::uint64_t generation {};
        TopoDS_Shape shape;
        double deviation {};
        double angularDeflection {};
        bool normalsFromUV {};
        Completion completion;
        std::shared_ptr<std::stop_source> cancellation;
    };

    struct Channel
    {
        std::uint64_t generation {};
        std::uint64_t activeGeneration {};
        bool active {false};
        Completion activeCompletion;
        std::shared_ptr<std::stop_source> activeCancellation;
        std::optional<Request> pending;
    };

    void request(
        const void* target,
        TopoDS_Shape shape,
        double deviation,
        double angularDeflection,
        bool normalsFromUV,
        Completion completion
    )
    {
        if (!target) {
            throw std::invalid_argument("Render mesh request requires a target identity");
        }
        if (!completion) {
            throw std::invalid_argument("Render mesh request requires a completion callback");
        }

        auto& channel = channels[target];
        Request next {
            ++lastGeneration,
            std::move(shape),
            deviation,
            angularDeflection,
            normalsFromUV,
            std::move(completion),
            std::make_shared<std::stop_source>()
        };
        channel.generation = next.generation;
        if (channel.active) {
            // Callback destruction can release presentation leases and enter
            // this controller again. Commit the replacement before releasing
            // the old capture, and never retain a map reference across it.
            auto replaced = std::exchange(channel.pending, std::move(next));
            auto cancellation = channel.activeCancellation;
            cancellation->request_stop();
            return;
        }
        start(target, std::move(next));
    }

    void cancel(const void* target)
    {
        const auto found = channels.find(target);
        if (found == channels.end()) {
            return;
        }
        auto& channel = found->second;
        channel.generation = ++lastGeneration;
        auto pending = std::move(channel.pending);
        channel.pending.reset();
        auto completion = std::move(channel.activeCompletion);
        auto cancellation = channel.activeCancellation;
        if (!channel.active) {
            channels.erase(found);
        }
        // A running worker retains only its shape and cancellation token, not
        // the provider/document. Cancelled owner captures can be released now;
        // leave the active channel until its worker completes to keep requests
        // for this target serialized. Release captures after all map access.
        if (cancellation) { cancellation->request_stop(); }
    }

    void cancelAll()
    {
        // Completion destruction can release a document presentation lease
        // and notify observers. Detach first so reentrant requests never modify
        // the map whose callbacks are being destroyed.
        decltype(channels) cancelled;
        cancelled.swap(channels);
        for (auto& [target, channel] : cancelled) {
            (void)target;
            if (channel.activeCancellation) {
                channel.activeCancellation->request_stop();
            }
        }
    }

private:
    void start(const void* target, Request request)
    {
        auto& channel = channels[target];
        channel.active = true;
        channel.activeGeneration = request.generation;
        channel.activeCompletion = std::move(request.completion);
        channel.activeCancellation = request.cancellation;

        const std::uint64_t generation = request.generation;
        const double deviation = request.deviation;
        const double angularDeflection = request.angularDeflection;
        const bool normalsFromUV = request.normalsFromUV;
        auto cancellation = std::move(request.cancellation);
        auto weakState = weak_from_this();
        const bool trace = qEnvironmentVariableIsSet("VIBECAD_RESTORE_DETAIL_TRACE");
        using Clock = std::chrono::steady_clock;
        const auto submitted = trace ? Clock::now() : Clock::time_point {};
        struct Prepared
        {
            RenderMeshResult result;
            Clock::time_point started;
            Clock::time_point prepared;
        };
        try {
            App::GetApplication().hostRuntime().submitWithCompletion(
                App::HostRuntime::Lane::Compute,
                [shape = std::move(request.shape),
                 deviation,
                 angularDeflection,
                 normalsFromUV,
                 trace,
                 cancellation](std::stop_token runtimeStop) mutable {
                    const auto started = trace ? Clock::now() : Clock::time_point {};
                    std::stop_callback stopWithRuntime(runtimeStop, [cancellation] {
                        cancellation->request_stop();
                    });
                    RenderMeshResult result;
                    try {
                        result.mesh = std::make_shared<const Part::RenderMesh>(
                            Part::prepareRenderMesh(
                                shape,
                                deviation,
                                angularDeflection,
                                normalsFromUV,
                                cancellation->get_token()
                            )
                        );
                    }
                    catch (const Standard_Failure& failure) {
                        const char* message = failure.GetMessageString();
                        result.error = message
                            ? message
                            : "OpenCASCADE render mesh preparation failed";
                    }
                    catch (const Base::Exception& failure) {
                        result.error = failure.what();
                    }
                    catch (const std::exception& failure) {
                        result.error = failure.what();
                    }
                    catch (...) {
                        result.error = "Unknown render mesh preparation failure";
                    }

                    if (cancellation->stop_requested()) {
                        result.mesh.reset();
                        result.error = "Render mesh preparation was cancelled";
                    }
                    return Prepared {std::move(result), started,
                                     trace ? Clock::now() : Clock::time_point {}};
                },
                [weakState, target, generation, trace, submitted](std::future<Prepared> future) {
                    Prepared output {{}, submitted, submitted};
                    try { output = future.get(); }
                    catch (const std::exception& failure) {
                        output.result.error = failure.what();
                        output.started = output.prepared = trace ? Clock::now() : Clock::time_point {};
                    }
                    catch (...) {
                        output.result.error = "Render mesh request cancelled or failed";
                        output.started = output.prepared = trace ? Clock::now() : Clock::time_point {};
                    }
                    Gui::dispatchToGuiFrame(
                        [weakState, target, generation, trace, submitted,
                         started = output.started, prepared = output.prepared,
                         result = std::move(output.result)]() mutable {
                            if (auto state = weakState.lock()) {
                                const auto adopting = trace ? Clock::now() : Clock::time_point {};
                                const auto vertices = result.mesh ? result.mesh->vertexCount() : 0;
                                const auto faces = result.mesh ? result.mesh->faceTriangleCounts.size() : 0;
                                state->finish(target, generation, std::move(result));
                                if (trace) {
                                    const auto milliseconds = [](auto begin, auto end) {
                                        return std::chrono::duration<double, std::milli>(end - begin).count();
                                    };
                                    Base::Console().message(
                                        "VIBECAD_RENDER request=%llu target=%p queue_ms=%.3f "
                                        "prepare_ms=%.3f dispatch_ms=%.3f adopt_ms=%.3f "
                                        "vertices=%zu faces=%zu\n",
                                        static_cast<unsigned long long>(generation), target,
                                        milliseconds(submitted, started),
                                        milliseconds(started, prepared),
                                        milliseconds(prepared, adopting),
                                        milliseconds(adopting, Clock::now()), vertices, faces
                                    );
                                }
                            }
                        }
                    );
                }
            );
        }
        catch (const std::exception& failure) {
            finish(target, generation, RenderMeshResult {{}, failure.what()});
        }
    }

    void finish(const void* target, std::uint64_t generation, RenderMeshResult result)
    {
        const auto found = channels.find(target);
        if (found == channels.end()) {
            return;
        }
        auto& channel = found->second;
        if (!channel.active || channel.activeGeneration != generation) {
            return;
        }

        channel.active = false;
        channel.activeCancellation.reset();
        Completion completion = std::move(channel.activeCompletion);
        const bool current = channel.generation == generation;
        if (current) {
            channels.erase(found);
            completion(std::move(result));
            return;
        }

        if (!channel.pending) {
            channels.erase(found);
            return;
        }
        Request pending = std::move(*channel.pending);
        channel.pending.reset();
        start(target, std::move(pending));
    }

    std::unordered_map<const void*, Channel> channels;
    // Target addresses can be reused after cancellation or destruction. Never
    // reuse a generation while an old completion may still be in the GUI queue.
    std::uint64_t lastGeneration {};
};

RenderMeshController::RenderMeshController()
    : state(std::make_shared<State>())
{}

RenderMeshController::~RenderMeshController()
{
    if (state) {
        state->cancelAll();
    }
}

void RenderMeshController::request(
    const void* target,
    TopoDS_Shape shape,
    double deviation,
    double angularDeflection,
    bool normalsFromUV,
    Completion completion
)
{
    requireGuiOwner();
    const auto owner = state;
    owner->request(
        target,
        std::move(shape),
        deviation,
        angularDeflection,
        normalsFromUV,
        std::move(completion)
    );
}

void RenderMeshController::cancel(const void* target)
{
    requireGuiOwner();
    const auto owner = state;
    owner->cancel(target);
}

void RenderMeshController::cancelAll()
{
    requireGuiOwner();
    const auto owner = state;
    owner->cancelAll();
}
