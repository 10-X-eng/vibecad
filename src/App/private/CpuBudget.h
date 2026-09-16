// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <algorithm>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <stop_token>

namespace App::detail
{

// One FIFO budget shared by native compute and isolated geometry workers.
// This is an internal scheduler primitive, not another executor or pool.
class CpuBudget
{
public:
    explicit CpuBudget(std::size_t capacity)
        : capacity_(std::max<std::size_t>(1, capacity)), available_(capacity_)
    {}

    bool acquire(std::size_t requested, std::stop_token stop)
    {
        const auto slots = std::clamp<std::size_t>(requested, 1, capacity_);
        std::unique_lock lock(mutex_);
        const auto ticket = nextTicket_++;
        waiters_.push_back(ticket);
        const bool ready = changed_.wait(lock, stop, [&] {
            return waiters_.front() == ticket && available_ >= slots;
        });
        if (!ready || stop.stop_requested()) {
            waiters_.erase(std::find(waiters_.begin(), waiters_.end(), ticket));
            lock.unlock();
            changed_.notify_all();
            return false;
        }
        waiters_.pop_front();
        available_ -= slots;
        lock.unlock();
        changed_.notify_all();
        return true;
    }

    void release(std::size_t slots)
    {
        {
            std::lock_guard lock(mutex_);
            available_ = std::min(capacity_, available_ + slots);
        }
        changed_.notify_all();
    }

    [[nodiscard]] std::size_t capacity() const { return capacity_; }

private:
    const std::size_t capacity_;
    std::size_t available_;
    std::uint64_t nextTicket_ {0};
    std::mutex mutex_;
    std::condition_variable_any changed_;
    std::deque<std::uint64_t> waiters_;
};

}  // namespace App::detail
