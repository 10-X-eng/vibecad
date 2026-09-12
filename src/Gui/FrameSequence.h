// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <coroutine>
#include <exception>
#include <memory>
#include <optional>
#include <stdexcept>
#include <utility>

namespace Gui
{
namespace detail
{
struct FrameSequenceControl
{
    std::coroutine_handle<> active;
};

template<typename Result>
struct FrameSequenceResult
{
    std::optional<Result> result;
    void return_value(Result value) { result.emplace(std::move(value)); }
    Result take() { return std::move(result.value()); }
};

template<>
struct FrameSequenceResult<void>
{
    void return_void() noexcept {}
    void take() noexcept {}
};
} // namespace detail

/** Owner-thread presentation with explicit suspension points.
 *
 * advance() resumes the deepest suspended child until the next yield. The
 * caller uses FrameBudget and the shared frame dispatcher; this type owns no
 * executor, threads, timers, or event loop. Nested operations retain ordinary
 * return values, exception handling, and scope destruction across frames.
 * Creation, advance, and destruction belong to the same GUI owner. Only
 * bounded Qt/Coin adoption belongs here; prepare expensive data on HostRuntime.
 */
template<typename Result = void>
class FrameSequence
{
public:
    struct promise_type: detail::FrameSequenceResult<Result>
    {
        detail::FrameSequenceControl* control {};
        std::coroutine_handle<> parent;
        std::exception_ptr failure;

        FrameSequence get_return_object()
        {
            return FrameSequence(Handle::from_promise(*this));
        }
        std::suspend_always initial_suspend() noexcept { return {}; }
        auto final_suspend() noexcept
        {
            struct Final
            {
                bool await_ready() const noexcept { return false; }
                std::coroutine_handle<> await_suspend(Handle finished) const noexcept
                {
                    auto& promise = finished.promise();
                    if (promise.parent) {
                        promise.control->active = promise.parent;
                        return promise.parent;
                    }
                    return std::noop_coroutine();
                }
                void await_resume() const noexcept {}
            };
            return Final {};
        }
        void unhandled_exception() noexcept { failure = std::current_exception(); }
    };
    using Handle = std::coroutine_handle<promise_type>;

    FrameSequence(FrameSequence&& other) noexcept
        : frame(std::exchange(other.frame, {})), control(std::move(other.control))
    {}
    FrameSequence(const FrameSequence&) = delete;
    FrameSequence& operator=(const FrameSequence&) = delete;
    ~FrameSequence() { if (frame) { frame.destroy(); } }

    bool advance()
    {
        if (!frame || frame.done()) { return false; }
        if (!control) {
            control = std::make_unique<detail::FrameSequenceControl>();
            control->active = frame;
            frame.promise().control = control.get();
        }
        control->active.resume();
        return !frame.done();
    }

    Result takeResult() { return take(frame); }

    struct Awaiter
    {
        Handle child;
        explicit Awaiter(Handle child) : child(child) {}
        Awaiter(Awaiter&& other) noexcept : child(std::exchange(other.child, {})) {}
        Awaiter(const Awaiter&) = delete;
        ~Awaiter() { if (child) { child.destroy(); } }
        bool await_ready() const noexcept { return false; }
        template<typename Promise>
        std::coroutine_handle<> await_suspend(std::coroutine_handle<Promise> parent) noexcept
        {
            child.promise().parent = parent;
            child.promise().control = parent.promise().control;
            child.promise().control->active = child;
            return child;
        }
        Result await_resume() { return take(child); }
    };

    auto operator co_await() &&
    {
        if (control) { throw std::logic_error("Cannot nest an already-started presentation sequence"); }
        return Awaiter {std::exchange(frame, {})};
    }

private:
    explicit FrameSequence(Handle frame) : frame(frame) {}
    static Result take(Handle frame)
    {
        if (!frame || !frame.done()) {
            throw std::logic_error("Presentation result requested before completion");
        }
        if (frame.promise().failure) { std::rethrow_exception(frame.promise().failure); }
        return frame.promise().take();
    }
    Handle frame;
    std::unique_ptr<detail::FrameSequenceControl> control;
};

} // namespace Gui
