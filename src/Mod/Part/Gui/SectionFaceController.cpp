// SPDX-License-Identifier: LGPL-2.1-or-later
#include "SectionFaceController.h"

#include <cstdint>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <utility>
#include <QApplication>
#include <QThread>
#include <Standard_Failure.hxx>
#include <App/Application.h>
#include <App/HostRuntime.h>
#include <Base/Exception.h>
#include <Gui/FrameBudget.h>

using namespace PartGui;

namespace
{
void requireOwner()
{
    if (!qApp || QThread::currentThread() != qApp->thread()) {
        throw std::logic_error("Section requests must originate on the GUI owner");
    }
}
}

struct SectionFaceController::State: std::enable_shared_from_this<State>
{
    struct Request
    {
        std::uint64_t generation;
        std::vector<SectionInstance> instances;
        Base::Vector3d origin;
        Base::Vector3d normal;
        Completion completion;
        std::optional<double> spacing;
    };

    std::uint64_t generation {0};
    bool active {false};
    std::optional<Request> pending;
    Completion completion;
    std::shared_ptr<std::stop_source> cancellation;

    void request(std::vector<SectionInstance> instances,
                 Base::Vector3d origin, Base::Vector3d normal, Completion callback,
                 std::optional<double> spacing = {})
    {
        if (!callback) {
            throw std::invalid_argument("Section request requires a completion callback");
        }
        Request next {++generation, std::move(instances), origin, normal, std::move(callback), spacing};
        if (active) {
            // Commit state before releasing callbacks: their destructors can
            // submit a replacement request on this owner.
            auto replaced = std::exchange(pending, std::move(next));
            auto oldCallback = std::move(completion);
            cancellation->request_stop();
            return;
        }
        start(std::move(next));
    }

    void cancel()
    {
        ++generation;
        auto discarded = std::exchange(pending, {});
        auto oldCallback = std::move(completion);
        if (cancellation) {
            cancellation->request_stop();
        }
    }

    void start(Request request)
    {
        active = true;
        completion = std::move(request.completion);
        cancellation = std::make_shared<std::stop_source>();
        auto stop = cancellation;
        auto weak = weak_from_this();
        const auto requestedGeneration = request.generation;
        auto deliver = [weak, requestedGeneration](SectionFaceResult result) {
            Gui::dispatchToGuiFrame([weak, requestedGeneration, result = std::move(result)]() mutable {
                if (auto owner = weak.lock()) {
                    owner->finish(requestedGeneration, std::move(result));
                }
            });
        };
        try {
            App::GetApplication().hostRuntime().submitWithCompletion(
                App::HostRuntime::Lane::Compute,
                [instances = std::move(request.instances), origin = request.origin,
                 normal = request.normal, spacing = request.spacing, stop](std::stop_token runtimeStop) {
                    std::stop_callback stopped(runtimeStop, [stop] { stop->request_stop(); });
                    SectionFaceResult result;
                    auto geometry = spacing ? std::make_shared<Part::SectionDisplayGeometry>() : nullptr;
                    for (const auto& instance : instances) {
                        if (stop->stop_requested()) {
                            break;
                        }
                        try {
                            auto face = instance.mesh
                                ? Part::prepareSectionMeshFaces(*instance.mesh, instance.transform,
                                                                origin, normal, stop->get_token())
                                : Part::prepareSectionFaces(instance.shape, instance.transform,
                                                           origin, normal, stop->get_token());
                            if (!face.IsNull()) {
                                if (geometry) {
                                    auto prepared = Part::prepareSectionDisplay(
                                        face, origin, normal, *spacing, stop->get_token());
                                    const auto append = [](auto& target, auto& source) {
                                        target.insert(target.end(), std::make_move_iterator(source.begin()),
                                                      std::make_move_iterator(source.end()));
                                    };
                                    append(geometry->triangles, prepared.triangles);
                                    append(geometry->hatch, prepared.hatch);
                                    append(geometry->outlines, prepared.outlines);
                                }
                                else {
                                    result.faces.push_back(std::move(face));
                                }
                            }
                        }
                        catch (const Standard_Failure& error) {
                            if (result.error.empty()) {
                                result.error = error.GetMessageString() ? error.GetMessageString()
                                                                        : "Section kernel failure";
                            }
                        }
                        catch (const Base::Exception& error) {
                            if (result.error.empty()) { result.error = error.what(); }
                        }
                        catch (const std::exception& error) {
                            if (result.error.empty()) { result.error = error.what(); }
                        }
                    }
                    if (stop->stop_requested()) {
                        result.faces.clear();
                        result.error = "Section request cancelled";
                    }
                    else {
                        result.geometry = std::move(geometry);
                    }
                    return result;
                },
                [deliver](std::future<SectionFaceResult> future) {
                    SectionFaceResult result;
                    try { result = future.get(); }
                    catch (const std::exception& error) { result.error = error.what(); }
                    catch (...) { result.error = "Section request cancelled or failed"; }
                    deliver(std::move(result));
                });
        }
        catch (const std::exception& error) {
            SectionFaceResult result;
            result.error = error.what();
            deliver(std::move(result));
        }
    }

    void finish(std::uint64_t requestedGeneration, SectionFaceResult result)
    {
        active = false;
        cancellation.reset();
        auto notify = std::move(completion);
        auto next = std::exchange(pending, {});
        if (next) {
            start(std::move(*next));
        }
        else if (requestedGeneration == generation && notify) {
            notify(std::move(result));
        }
    }
};

SectionFaceController::SectionFaceController() : state(std::make_shared<State>()) {}
SectionFaceController::~SectionFaceController() { state->cancel(); }

void SectionFaceController::request(std::vector<SectionInstance> instances,
                                   Base::Vector3d origin, Base::Vector3d normal, Completion completion)
{
    requireOwner();
    const auto owner = state;
    owner->request(std::move(instances), origin, normal, std::move(completion));
}

void SectionFaceController::cancel()
{
    requireOwner();
    const auto owner = state;
    owner->cancel();
}

void SectionFaceController::requestDisplay(std::vector<SectionInstance> instances,
                                         Base::Vector3d origin, Base::Vector3d normal,
                                         double spacing, Completion completion)
{
    requireOwner();
    const auto owner = state;
    owner->request(std::move(instances), origin, normal, std::move(completion), spacing);
}
