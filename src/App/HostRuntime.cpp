// SPDX-License-Identifier: LGPL-2.1-or-later

#include "HostRuntime.h"
#include "private/CpuBudget.h"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <latch>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <Base/Console.h>

#include <QByteArray>
#include <QProcess>
#include <QProcessEnvironment>
#include <QString>
#include <QStringList>

#ifdef Q_OS_WIN
# include <windows.h>
# include <psapi.h>
#elif defined(Q_OS_MACOS)
# include <libproc.h>
#endif

using namespace App;

namespace
{

constexpr auto isolationProtocol = "VIBECAD-ISOLATION/1";
constexpr qsizetype isolationOutputTailBytes = 64 * 1024;

using App::detail::CpuBudget;

class CpuLease
{
public:
    CpuLease(CpuBudget& budget, std::size_t slots)
        : budget_(&budget)
        , slots_(slots)
    {}

    ~CpuLease()
    {
        budget_->release(slots_);
    }

    CpuLease(const CpuLease&) = delete;
    CpuLease& operator=(const CpuLease&) = delete;

private:
    CpuBudget* budget_;
    std::size_t slots_;
};

struct IsolationJob
{
    IsolationJob(
        std::uint64_t jobId,
        std::string jobRequest,
        std::size_t jobMemoryLimitBytes,
        std::size_t jobCpuSlots
    )
        : id(jobId)
        , request(std::move(jobRequest))
        , memoryLimitBytes(jobMemoryLimitBytes)
        , cpuSlots(jobCpuSlots)
    {}

    void finish(HostRuntime::IsolationResult result)
    {
        bool expected = false;
        if (finished.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
            completion.set_value(std::move(result));
        }
    }

    const std::uint64_t id;
    const std::string request;
    const std::size_t memoryLimitBytes;
    const std::size_t cpuSlots;
    std::promise<HostRuntime::IsolationResult> completion;
    std::stop_source cancellation;
    std::atomic_bool finished {false};
    std::atomic_bool memoryExceeded {false};
    std::atomic_size_t observedMemoryBytes {0};
};

bool sameConfiguration(
    const HostRuntime::IsolationConfiguration& left,
    const HostRuntime::IsolationConfiguration& right
)
{
    return left.executable == right.executable && left.arguments == right.arguments
        && left.workingDirectory == right.workingDirectory
        && left.environment == right.environment && left.workerCount == right.workerCount;
}

void appendOutputTail(QByteArray& tail, const QByteArray& bytes)
{
    tail.append(bytes);
    if (tail.size() > isolationOutputTailBytes) {
        tail.remove(0, tail.size() - isolationOutputTailBytes);
    }
}

std::size_t processMemoryBytes(qint64 processId)
{
    if (processId <= 0) {
        return 0;
    }
#ifdef Q_OS_WIN
    const HANDLE process = OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
        FALSE,
        static_cast<DWORD>(processId)
    );
    if (!process) {
        return 0;
    }
    PROCESS_MEMORY_COUNTERS counters {};
    counters.cb = sizeof(counters);
    const bool read = GetProcessMemoryInfo(process, &counters, sizeof(counters)) != FALSE;
    CloseHandle(process);
    return read ? static_cast<std::size_t>(counters.WorkingSetSize) : 0;
#elif defined(Q_OS_MACOS)
    rusage_info_v2 usage {};
    if (proc_pid_rusage(
            static_cast<int>(processId),
            RUSAGE_INFO_V2,
            reinterpret_cast<rusage_info_t*>(&usage)
        ) != 0) {
        return 0;
    }
    return static_cast<std::size_t>(usage.ri_resident_size);
#elif defined(Q_OS_LINUX)
    std::ifstream status("/proc/" + std::to_string(processId) + "/status");
    std::string line;
    while (std::getline(status, line)) {
        if (line.starts_with("VmRSS:")) {
            std::istringstream fields(line.substr(6));
            std::size_t kibibytes = 0;
            fields >> kibibytes;
            return kibibytes * 1024;
        }
    }
    return 0;
#else
    return 0;
#endif
}

}  // namespace

class HostRuntime::Private
{
public:
    struct LaneState
    {
        LaneState(std::size_t initialWorkers, std::size_t capacity)
            : workerLimit(initialWorkers)
            , workerCapacity(capacity)
        {
            workers.reserve(capacity);
        }

        std::atomic_size_t workerLimit;
        const std::size_t workerCapacity;
        std::atomic_size_t ready {0};
        std::atomic_size_t queued {0};
        std::atomic_size_t active {0};
        std::mutex queueMutex;
        std::condition_variable workAvailable;
        std::deque<Work> queue;
        std::mutex workersMutex;
        std::vector<std::thread> workers;
    };

    explicit Private(
        std::size_t requestedLogicalProcessors,
        bool isolationChild
    )
        : logicalProcessors(normalizeLogicalProcessors(requestedLogicalProcessors))
        , compute(
              isolationChild ? 1 : HostRuntime::workerBudget(logicalProcessors),
              HostRuntime::workerBudget(logicalProcessors)
          )
        , io(
              isolationChild ? 1 : HostRuntime::ioWorkerBudget(logicalProcessors),
              HostRuntime::ioWorkerBudget(logicalProcessors)
          )
        , document(
              isolationChild ? 1 : HostRuntime::documentWorkerBudget(logicalProcessors),
              HostRuntime::documentWorkerBudget(logicalProcessors)
          )
        , cpuBudget(compute.workerCapacity)
    {
        std::latch workersStarted(
            compute.workerLimit.load() + io.workerLimit.load() + document.workerLimit.load()
        );
        try {
            startLane(Lane::Compute, compute, compute.workerLimit.load(), &workersStarted);
            startLane(Lane::Io, io, io.workerLimit.load(), &workersStarted);
            startLane(Lane::Document, document, document.workerLimit.load(), &workersStarted);
        }
        catch (...) {
            stopAndJoin();
            throw;
        }
        workersStarted.wait();
    }

    ~Private()
    {
        shutdown();
    }

    static std::size_t normalizeLogicalProcessors(std::size_t requested)
    {
        if (requested != 0) {
            return requested;
        }
        return std::max<std::size_t>(1, std::thread::hardware_concurrency());
    }

    void enqueue(Lane lane, Work work)
    {
        if (!work) {
            throw std::invalid_argument("HostRuntime cannot submit empty work");
        }
        auto& target = laneState(lane);
        {
            std::lock_guard lock(target.queueMutex);
            if (!accepting.load(std::memory_order_acquire)) {
                throw std::runtime_error("HostRuntime is shutting down");
            }
            if (lane == Lane::Compute && activeRuntime == this && activeLane == Lane::Compute) {
                target.queue.emplace_front(std::move(work));
            }
            else {
                target.queue.emplace_back(std::move(work));
            }
            target.queued.fetch_add(1, std::memory_order_release);
        }
        target.workAvailable.notify_one();
    }

    void startLane(
        Lane laneKind,
        LaneState& lane,
        std::size_t count,
        std::latch* workersStarted
    )
    {
        for (std::size_t index = 0; index < count; ++index) {
            lane.workers.emplace_back([this, laneKind, &lane, workersStarted] {
                workerLoop(laneKind, lane, workersStarted);
            });
        }
    }

    void workerLoop(Lane laneKind, LaneState& lane, std::latch* workersStarted)
    {
        activeRuntime = this;
        activeLane = laneKind;
        lane.ready.fetch_add(1, std::memory_order_release);
        if (workersStarted) {
            workersStarted->count_down();
        }

        while (true) {
            Work work;
            {
                std::unique_lock lock(lane.queueMutex);
                lane.workAvailable.wait(lock, [this, &lane] {
                    return !lane.queue.empty() || !accepting.load(std::memory_order_acquire);
                });
                if (!accepting.load(std::memory_order_acquire)) {
                    break;
                }
                work = std::move(lane.queue.front());
                lane.queue.pop_front();
                lane.queued.fetch_sub(1, std::memory_order_release);
                lane.active.fetch_add(1, std::memory_order_release);
            }

            if (laneKind == Lane::Compute) {
                if (cpuBudget.acquire(1, stopSource.get_token())) {
                    CpuLease permit(cpuBudget, 1);
                    work(stopSource.get_token());
                }
                else {
                    work.abandon();
                }
            }
            else {
                work(stopSource.get_token());
            }
            lane.active.fetch_sub(1, std::memory_order_release);
        }

        lane.ready.fetch_sub(1, std::memory_order_release);
        activeRuntime = nullptr;
    }

    void ensureComputeWorkers(std::size_t requested)
    {
        const auto target = std::clamp<std::size_t>(requested, 1, compute.workerCapacity);
        std::unique_lock lock(compute.workersMutex);
        const auto current = compute.workers.size();
        if (target <= current) {
            return;
        }
        std::latch workersStarted(target - current);
        startLane(Lane::Compute, compute, target - current, &workersStarted);
        compute.workerLimit.store(target, std::memory_order_release);
        lock.unlock();
        workersStarted.wait();
    }

    [[nodiscard]] bool tryRunOnePendingComputeTask()
    {
        if (activeRuntime != this || activeLane != Lane::Compute) {
            return false;
        }

        Work work;
        {
            std::lock_guard lock(compute.queueMutex);
            if (compute.queue.empty()) {
                return false;
            }
            work = std::move(compute.queue.front());
            compute.queue.pop_front();
            compute.queued.fetch_sub(1, std::memory_order_release);
        }
        work(stopSource.get_token());
        return true;
    }

    void shutdown()
    {
        std::call_once(shutdownOnce, [this] {
            stopAndJoin();
        });
    }

    void stopAndJoin()
    {
        accepting.store(false, std::memory_order_release);
        stopSource.request_stop();
        clearQueue(compute);
        clearQueue(io);
        clearQueue(document);
        compute.workAvailable.notify_all();
        io.workAvailable.notify_all();
        document.workAvailable.notify_all();
        stopIsolationWorkers();
        joinLane(compute);
        joinLane(io);
        joinLane(document);
    }

    static void clearQueue(LaneState& lane)
    {
        std::deque<Work> cancelled;
        {
            std::lock_guard lock(lane.queueMutex);
            cancelled.swap(lane.queue);
            lane.queued.store(0, std::memory_order_release);
        }
        for (const auto& work : cancelled) {
            work.abandon();
        }
    }

    static void joinLane(LaneState& lane)
    {
        std::lock_guard lock(lane.workersMutex);
        for (auto& worker : lane.workers) {
            if (worker.joinable()) {
                worker.join();
            }
        }
        lane.workers.clear();
    }

    LaneState& laneState(Lane lane)
    {
        switch (lane) {
            case Lane::Compute:
                return compute;
            case Lane::Io:
                return io;
            case Lane::Document:
                return document;
        }
        throw std::logic_error("Unknown HostRuntime lane");
    }

    const LaneState& laneState(Lane lane) const
    {
        switch (lane) {
            case Lane::Compute:
                return compute;
            case Lane::Io:
                return io;
            case Lane::Document:
                return document;
        }
        throw std::logic_error("Unknown HostRuntime lane");
    }

    void startIsolationWorkers(IsolationConfiguration requested)
    {
        if (requested.executable.empty()) {
            throw std::invalid_argument("Isolation worker executable cannot be empty");
        }
        if (requested.workerCount == 0) {
            requested.workerCount = HostRuntime::isolationWorkerBudget(logicalProcessors);
        }
        std::lock_guard lock(isolationMutex);
        if (isolationConfiguration) {
            if (!sameConfiguration(*isolationConfiguration, requested)) {
                throw std::logic_error(
                    "HostRuntime isolation workers are already configured differently"
                );
            }
            return;
        }
        if (!accepting.load(std::memory_order_acquire)) {
            throw std::runtime_error("HostRuntime is shutting down");
        }
        isolationConfiguration = std::move(requested);
        isolationWorkers.reserve(isolationConfiguration->workerCount);
        for (std::size_t index = 0; index < isolationConfiguration->workerCount; ++index) {
            isolationWorkers.emplace_back([this] { isolationWorkerLoop(); });
        }
    }

    IsolationSubmission submitIsolation(
        std::string request,
        std::size_t memoryLimitBytes,
        std::size_t cpuSlots
    )
    {
        if (request.empty() || request.find_first_of("\r\n") != std::string::npos) {
            throw std::invalid_argument(
                "Isolation request must be one non-empty protocol-safe line"
            );
        }
        std::shared_ptr<IsolationJob> job;
        {
            std::lock_guard configurationLock(isolationMutex);
            if (!isolationConfiguration) {
                throw std::logic_error("HostRuntime isolation workers are not configured");
            }
        }
        {
            std::lock_guard lock(isolationQueueMutex);
            if (!accepting.load(std::memory_order_acquire)) {
                throw std::runtime_error("HostRuntime is shutting down");
            }
            const auto id = nextIsolationId.fetch_add(1, std::memory_order_relaxed);
            job = std::make_shared<IsolationJob>(
                id,
                std::move(request),
                memoryLimitBytes,
                std::clamp<std::size_t>(cpuSlots, 1, cpuBudget.capacity())
            );
            isolationJobs.emplace(id, job);
            isolationQueue.push_back(job);
            isolationQueued.fetch_add(1, std::memory_order_release);
        }
        auto future = job->completion.get_future();
        isolationAvailable.notify_one();
        return IsolationSubmission {job->id, std::move(future)};
    }

    bool cancelIsolation(std::uint64_t id)
    {
        std::shared_ptr<IsolationJob> job;
        {
            std::lock_guard lock(isolationQueueMutex);
            const auto iterator = isolationJobs.find(id);
            if (iterator == isolationJobs.end() || iterator->second->finished.load()) {
                return false;
            }
            job = iterator->second;
        }
        // Stop callbacks run synchronously; never invoke them under the queue
        // mutex that completion uses to remove this job.
        job->cancellation.request_stop();
        isolationAvailable.notify_all();
        return true;
    }

    void stopIsolationWorkers()
    {
        std::deque<std::shared_ptr<IsolationJob>> queued;
        decltype(isolationJobs) jobs;
        {
            std::lock_guard lock(isolationQueueMutex);
            queued.swap(isolationQueue);
            jobs.swap(isolationJobs);
            isolationQueued.store(0, std::memory_order_release);
        }
        for (const auto& [id, job] : jobs) {
            (void)id;
            job->cancellation.request_stop();
        }
        for (const auto& job : queued) {
            job->finish(IsolationResult {
                IsolationStatus::Cancelled,
                {},
                "HostRuntime shut down before the isolation job started",
                {},
            });
        }
        isolationAvailable.notify_all();
        for (auto& worker : isolationWorkers) {
            if (worker.joinable()) {
                worker.join();
            }
        }
        isolationWorkers.clear();
    }

    std::optional<std::string> waitForProtocolLine(
        QProcess& process,
        const std::string& prefix,
        QByteArray& outputTail,
        const std::shared_ptr<IsolationJob>& job = {}
    )
    {
        while (accepting.load(std::memory_order_acquire)
               && (!job || !job->cancellation.stop_requested())) {
            std::size_t linesRead = 0;
            while (process.canReadLine() && linesRead < 256) {
                const QByteArray line = process.readLine();
                ++linesRead;
                const std::string text = line.trimmed().toStdString();
                if (text.starts_with(prefix)) {
                    return text.substr(prefix.size());
                }
                appendOutputTail(outputTail, line);
            }
            if (process.state() == QProcess::NotRunning) {
                appendOutputTail(outputTail, process.readAll());
                return std::nullopt;
            }
            if (job && job->memoryLimitBytes > 0) {
                const auto observed = processMemoryBytes(process.processId());
                job->observedMemoryBytes.store(observed, std::memory_order_release);
                if (observed > job->memoryLimitBytes) {
                    job->memoryExceeded.store(true, std::memory_order_release);
                    return std::nullopt;
                }
            }
            process.waitForReadyRead(100);
        }
        return std::nullopt;
    }

    bool startIsolationProcess(QProcess& process, QByteArray& outputTail)
    {
        const auto& configuration = *isolationConfiguration;
        process.setProgram(QString::fromUtf8(configuration.executable));
        QStringList arguments;
        for (const auto& argument : configuration.arguments) {
            arguments.push_back(QString::fromUtf8(argument));
        }
        process.setArguments(arguments);
        if (!configuration.workingDirectory.empty()) {
            process.setWorkingDirectory(QString::fromUtf8(configuration.workingDirectory));
        }
        auto environment = QProcessEnvironment::systemEnvironment();
        for (const auto& [name, value] : configuration.environment) {
            environment.insert(QString::fromUtf8(name), QString::fromUtf8(value));
        }
        process.setProcessEnvironment(environment);
        process.setProcessChannelMode(QProcess::MergedChannels);
#ifdef Q_OS_WIN
        process.setCreateProcessArgumentsModifier([](QProcess::CreateProcessArguments* args) {
            args->flags |= CREATE_NO_WINDOW;
        });
#endif
        process.start(QIODevice::ReadWrite);
        if (!process.waitForStarted(-1)) {
            appendOutputTail(outputTail, process.errorString().toUtf8());
            return false;
        }
        return waitForProtocolLine(
                   process,
                   std::string(isolationProtocol) + " READY",
                   outputTail
               )
            .has_value();
    }

    void finishIsolationJob(
        const std::shared_ptr<IsolationJob>& job,
        IsolationResult result
    )
    {
        job->finish(std::move(result));
        std::lock_guard lock(isolationQueueMutex);
        isolationJobs.erase(job->id);
    }

    void isolationWorkerLoop()
    {
        QProcess process;
        QByteArray startupOutput;
        bool processReady = startIsolationProcess(process, startupOutput);
        if (processReady) {
            isolationReady.fetch_add(1, std::memory_order_release);
        }

        while (accepting.load(std::memory_order_acquire)) {
            std::shared_ptr<IsolationJob> job;
            {
                std::unique_lock lock(isolationQueueMutex);
                isolationAvailable.wait(lock, [this] {
                    return !isolationQueue.empty()
                        || !accepting.load(std::memory_order_acquire);
                });
                if (!accepting.load(std::memory_order_acquire)) {
                    break;
                }
                job = std::move(isolationQueue.front());
                isolationQueue.pop_front();
                isolationQueued.fetch_sub(1, std::memory_order_release);
            }

            if (job->cancellation.stop_requested()) {
                finishIsolationJob(
                    job,
                    IsolationResult {
                        IsolationStatus::Cancelled,
                        {},
                        "Isolation job was cancelled before execution",
                        {},
                    }
                );
                continue;
            }
            if (!processReady) {
                startupOutput.clear();
                processReady = startIsolationProcess(process, startupOutput);
                if (processReady) {
                    isolationReady.fetch_add(1, std::memory_order_release);
                }
            }
            if (!processReady) {
                finishIsolationJob(
                    job,
                    IsolationResult {
                        IsolationStatus::Failed,
                        {},
                        "Isolation worker failed to start",
                        startupOutput.toStdString(),
                        false,
                        0,
                    }
                );
                continue;
            }

            std::stop_callback stopWithRuntime(stopSource.get_token(), [&job] {
                job->cancellation.request_stop();
            });
            const bool admitted = cpuBudget.acquire(job->cpuSlots, job->cancellation.get_token());
            if (!admitted) {
                finishIsolationJob(
                    job,
                    IsolationResult {
                        IsolationStatus::Cancelled,
                        {},
                        "Isolation job was cancelled before CPU admission",
                        {},
                    }
                );
                continue;
            }
            CpuLease cpuPermit(cpuBudget, job->cpuSlots);

            isolationActive.fetch_add(1, std::memory_order_release);
            QByteArray outputTail;
            const std::string command = std::string(isolationProtocol) + " JOB "
                + std::to_string(job->id) + " " + std::to_string(job->cpuSlots) + " "
                + job->request + "\n";
            process.write(QByteArray::fromStdString(command));
            while (process.bytesToWrite() > 0 && process.state() != QProcess::NotRunning
                   && accepting.load(std::memory_order_acquire)
                   && !job->cancellation.stop_requested()) {
                process.waitForBytesWritten(100);
            }

            const std::string responsePrefix = std::string(isolationProtocol) + " RESULT "
                + std::to_string(job->id) + " ";
            const auto response = waitForProtocolLine(
                process,
                responsePrefix,
                outputTail,
                job
            );
            isolationActive.fetch_sub(1, std::memory_order_release);

            if (job->cancellation.stop_requested()
                || !accepting.load(std::memory_order_acquire)) {
                process.kill();
                process.waitForFinished(-1);
                isolationReady.fetch_sub(1, std::memory_order_release);
                processReady = false;
                finishIsolationJob(
                    job,
                    IsolationResult {
                        IsolationStatus::Cancelled,
                        {},
                        "Isolation job was cancelled",
                        outputTail.toStdString(),
                        false,
                        job->observedMemoryBytes.load(std::memory_order_acquire),
                    }
                );
                if (accepting.load(std::memory_order_acquire)) {
                    startupOutput.clear();
                    processReady = startIsolationProcess(process, startupOutput);
                    if (processReady) {
                        isolationReady.fetch_add(1, std::memory_order_release);
                    }
                }
                continue;
            }
            if (job->memoryExceeded.load(std::memory_order_acquire)) {
                process.kill();
                process.waitForFinished(-1);
                isolationReady.fetch_sub(1, std::memory_order_release);
                processReady = false;
                finishIsolationJob(
                    job,
                    IsolationResult {
                        IsolationStatus::Failed,
                        {},
                        "Isolation job exceeded its memory limit",
                        outputTail.toStdString(),
                        true,
                        job->observedMemoryBytes.load(std::memory_order_acquire),
                    }
                );
                startupOutput.clear();
                processReady = startIsolationProcess(process, startupOutput);
                if (processReady) {
                    isolationReady.fetch_add(1, std::memory_order_release);
                }
                continue;
            }
            if (!response) {
                if (processReady) {
                    isolationReady.fetch_sub(1, std::memory_order_release);
                }
                processReady = false;
                finishIsolationJob(
                    job,
                    IsolationResult {
                        IsolationStatus::Failed,
                        {},
                        "Isolation worker exited before returning a result",
                        outputTail.toStdString(),
                        false,
                        job->observedMemoryBytes.load(std::memory_order_acquire),
                    }
                );
                startupOutput.clear();
                processReady = startIsolationProcess(process, startupOutput);
                if (processReady) {
                    isolationReady.fetch_add(1, std::memory_order_release);
                }
                continue;
            }
            finishIsolationJob(
                job,
                IsolationResult {
                    IsolationStatus::Completed,
                    *response,
                    {},
                    outputTail.toStdString(),
                    false,
                    job->observedMemoryBytes.load(std::memory_order_acquire),
                }
            );
        }

        if (process.state() != QProcess::NotRunning) {
            process.write(QByteArray(isolationProtocol) + " SHUTDOWN\n");
            process.waitForBytesWritten(250);
            process.terminate();
            if (!process.waitForFinished(1000)) {
                process.kill();
                process.waitForFinished(-1);
            }
        }
        if (processReady) {
            isolationReady.fetch_sub(1, std::memory_order_release);
        }
    }

    const std::size_t logicalProcessors;
    LaneState compute;
    LaneState io;
    LaneState document;
    CpuBudget cpuBudget;
    std::atomic_bool accepting {true};
    std::stop_source stopSource;
    std::once_flag shutdownOnce;

    std::mutex isolationMutex;
    std::optional<IsolationConfiguration> isolationConfiguration;
    std::vector<std::thread> isolationWorkers;
    std::mutex isolationQueueMutex;
    std::condition_variable isolationAvailable;
    std::deque<std::shared_ptr<IsolationJob>> isolationQueue;
    std::unordered_map<std::uint64_t, std::shared_ptr<IsolationJob>> isolationJobs;
    std::atomic_uint64_t nextIsolationId {1};
    std::atomic_size_t isolationReady {0};
    std::atomic_size_t isolationQueued {0};
    std::atomic_size_t isolationActive {0};

    static thread_local Private* activeRuntime;
    static thread_local Lane activeLane;
};

thread_local HostRuntime::Private* HostRuntime::Private::activeRuntime = nullptr;
thread_local HostRuntime::Lane HostRuntime::Private::activeLane = HostRuntime::Lane::Io;

HostRuntime::HostRuntime(std::size_t logicalProcessors, bool isolationChild)
    : d(std::make_unique<Private>(logicalProcessors, isolationChild))
{}

HostRuntime::~HostRuntime() = default;

void HostRuntime::shutdown()
{
    d->shutdown();
}

bool HostRuntime::isAccepting() const
{
    return d->accepting.load(std::memory_order_acquire);
}

std::size_t HostRuntime::logicalProcessorCount() const
{
    return d->logicalProcessors;
}

std::size_t HostRuntime::workerCount() const
{
    return workerCount(Lane::Compute);
}

std::size_t HostRuntime::workerCount(Lane lane) const
{
    return d->laneState(lane).workerLimit.load(std::memory_order_acquire);
}

std::size_t HostRuntime::readyWorkerCount() const
{
    return readyWorkerCount(Lane::Compute);
}

std::size_t HostRuntime::readyWorkerCount(Lane lane) const
{
    return d->laneState(lane).ready.load(std::memory_order_acquire);
}

std::size_t HostRuntime::queuedTaskCount() const
{
    return queuedTaskCount(Lane::Compute);
}

std::size_t HostRuntime::queuedTaskCount(Lane lane) const
{
    return d->laneState(lane).queued.load(std::memory_order_acquire);
}

std::size_t HostRuntime::activeTaskCount() const
{
    return activeTaskCount(Lane::Compute);
}

std::size_t HostRuntime::activeTaskCount(Lane lane) const
{
    return d->laneState(lane).active.load(std::memory_order_acquire);
}

void HostRuntime::parallelFor(
    std::size_t count,
    const std::function<void(std::size_t)>& function,
    std::size_t concurrency
)
{
    if (!function) {
        throw std::invalid_argument("HostRuntime cannot execute empty parallel work");
    }
    if (count == 0) {
        return;
    }

    const auto requested = concurrency == 0 ? workerCount(Lane::Compute) : concurrency;
    const auto width = std::min(
        count,
        std::clamp<std::size_t>(requested, 1, d->compute.workerCapacity)
    );
    d->ensureComputeWorkers(width);

    std::atomic_size_t next {0};
    std::atomic_size_t completed {0};
    const auto grain = std::max<std::size_t>(1, count / (width * 4));
    std::vector<std::future<void>> work;
    work.reserve(width);
    std::exception_ptr failure;
    try {
        for (std::size_t worker = 0; worker < width; ++worker) {
            work.push_back(submit(Lane::Compute, [&, grain](std::stop_token stopToken) {
                std::size_t processed = 0;
                while (!stopToken.stop_requested()) {
                    const auto begin = next.fetch_add(grain, std::memory_order_relaxed);
                    if (begin >= count) {
                        break;
                    }
                    const auto end = std::min(count, begin + grain);
                    for (auto index = begin; index < end; ++index) {
                        if (stopToken.stop_requested()) {
                            break;
                        }
                        Base::CancellationScope::check();
                        function(index);
                        ++processed;
                    }
                }
                completed.fetch_add(processed, std::memory_order_relaxed);
            }));
        }
    }
    catch (...) {
        // Submission can fail after earlier callbacks started (shutdown or
        // allocation failure). Those callbacks borrow next and function:
        // retain this frame and join them before propagating the admission
        // error. A packaged-task future's destructor does not join its work.
        failure = std::current_exception();
    }

    for (auto& item : work) {
        try {
            wait(item);
        }
        catch (...) {
            if (!failure) {
                failure = std::current_exception();
            }
        }
    }
    if (failure) {
        std::rethrow_exception(failure);
    }
    // A running packaged task can return normally after observing shutdown.
    // Its ready future is not proof that every input was computed. Never let
    // a caller adopt partially filled geometry/projection buffers as success.
    if (completed.load(std::memory_order_relaxed) != count) {
        throw std::runtime_error("Parallel computation was cancelled before all items completed");
    }
}

void HostRuntime::startIsolationWorkers(IsolationConfiguration configuration)
{
    d->startIsolationWorkers(std::move(configuration));
}

HostRuntime::IsolationSubmission HostRuntime::submitIsolation(
    std::string request,
    std::size_t memoryLimitBytes,
    std::size_t cpuSlots
)
{
    return d->submitIsolation(std::move(request), memoryLimitBytes, cpuSlots);
}

bool HostRuntime::cancelIsolation(std::uint64_t id)
{
    return d->cancelIsolation(id);
}

std::size_t HostRuntime::isolationWorkerCount() const
{
    std::lock_guard lock(d->isolationMutex);
    return d->isolationConfiguration ? d->isolationConfiguration->workerCount : 0;
}

std::size_t HostRuntime::readyIsolationWorkerCount() const
{
    return d->isolationReady.load(std::memory_order_acquire);
}

std::size_t HostRuntime::queuedIsolationTaskCount() const
{
    return d->isolationQueued.load(std::memory_order_acquire);
}

std::size_t HostRuntime::activeIsolationTaskCount() const
{
    return d->isolationActive.load(std::memory_order_acquire);
}

std::size_t HostRuntime::workerBudget(std::size_t logicalProcessors)
{
    const auto normalized = std::max<std::size_t>(1, logicalProcessors);
    const auto wholeGroups = (normalized / 4) * 3;
    const auto remainder = ((normalized % 4) * 3) / 4;
    return std::max<std::size_t>(1, wholeGroups + remainder);
}

std::size_t HostRuntime::ioWorkerBudget(std::size_t logicalProcessors)
{
    const auto normalized = std::max<std::size_t>(1, logicalProcessors);
    return std::max<std::size_t>(1, normalized - workerBudget(normalized));
}

std::size_t HostRuntime::documentWorkerBudget(std::size_t logicalProcessors)
{
    return ioWorkerBudget(logicalProcessors);
}

std::size_t HostRuntime::isolationWorkerBudget(std::size_t logicalProcessors)
{
    return ioWorkerBudget(logicalProcessors);
}

void HostRuntime::Work::operator()(std::stop_token stop) const noexcept
{
    try { execute(stop); }
    catch (const std::exception& error) {
        Base::Console().error("HostRuntime completion failed: %s\n", error.what());
    }
    catch (...) { Base::Console().error("HostRuntime completion failed\n"); }
}

void HostRuntime::Work::abandon() const noexcept
{
    try { if (cancelled) { cancelled(); } }
    catch (const std::exception& error) {
        Base::Console().error("HostRuntime cancellation completion failed: %s\n", error.what());
    }
    catch (...) { Base::Console().error("HostRuntime cancellation completion failed\n"); }
}

void HostRuntime::enqueue(Lane lane, Work work)
{
    d->enqueue(lane, std::move(work));
}

bool HostRuntime::tryRunOnePendingComputeTask()
{
    return d->tryRunOnePendingComputeTask();
}
