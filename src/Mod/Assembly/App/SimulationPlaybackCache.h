// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <optional>
#include <string>
#include <vector>
#include <utility>
#include <Mod/Assembly/AssemblyGlobal.h>
#include "SimulationFrameSnapshot.h"

namespace MbD { class ASMTAssembly; }

namespace Assembly::detail
{
// Private, disposable playback data, never a replacement for a solved engine.
// Call only on a worker with detached inputs. The key covers solver inputs,
// binding order/offsets and the producer version; files authenticate their payload.
class AssemblyExport SimulationPlaybackCache
{
public:
    using Binding = std::pair<std::string, Base::Placement>;
    static std::string inputKey(
        const MbD::ASMTAssembly& assembly, const std::vector<Binding>& bindings,
        const std::string& producer);
    explicit SimulationPlaybackCache(std::string directory);
    void store(const std::string& key, const std::vector<SimulationFrameTrack>& tracks) const;
    std::optional<std::vector<SimulationFrameTrack>> load(
        const std::string& key, const std::vector<Base::Placement>& offsets) const;

private:
    std::string directory;
};
}
