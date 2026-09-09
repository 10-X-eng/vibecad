// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <chrono>
#include <cctype>
#include <iostream>
#include <string>
#include <thread>

#ifdef _WIN32
# include <process.h>
#else
# include <unistd.h>
#endif

namespace
{

constexpr auto protocol = "VIBECAD-ISOLATION/1";

long processId()
{
#ifdef _WIN32
    return static_cast<long>(_getpid());
#else
    return static_cast<long>(getpid());
#endif
}

}  // namespace

int main()
{
    std::cout << protocol << " READY" << std::endl;
    std::string line;
    std::size_t sequence = 0;
    while (std::getline(std::cin, line)) {
        if (line == std::string(protocol) + " SHUTDOWN") {
            return 0;
        }
        const std::string prefix = std::string(protocol) + " JOB ";
        if (!line.starts_with(prefix)) {
            continue;
        }
        const auto separator = line.find(' ', prefix.size());
        if (separator == std::string::npos) {
            continue;
        }
        const auto id = line.substr(prefix.size(), separator - prefix.size());
        auto payload = line.substr(separator + 1);
        const auto cpuSeparator = payload.find(' ');
        if (cpuSeparator != std::string::npos
            && !payload.substr(0, cpuSeparator).empty()
            && std::ranges::all_of(
                payload.substr(0, cpuSeparator),
                [](unsigned char value) { return std::isdigit(value) != 0; }
            )) {
            payload = payload.substr(cpuSeparator + 1);
        }
        if (payload.starts_with("sleep:")) {
            std::this_thread::sleep_for(
                std::chrono::milliseconds(std::stoul(payload.substr(6)))
            );
        }
        ++sequence;
        std::cout << protocol << " RESULT " << id << ' ' << processId() << ':'
                  << sequence << ':' << payload << std::endl;
    }
    return 0;
}
