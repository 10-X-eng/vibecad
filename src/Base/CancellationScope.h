// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <stop_token>
#include <vector>
#include <FCGlobal.h>

namespace Base
{

/** Cancellation inherited by synchronous callees within one worker phase.
 *
 * Scopes never cross threads; captured tokens travel with submitted work.
 * Nested scopes check every parent, allowing both operation cancellation
 * and runtime shutdown to stop shared parsing/compute routines. Storage and
 * checks live in FreeCADBase so all Windows modules observe the same scope.
 */
class BaseExport CancellationScope final
{
public:
    struct Captured
    {
        std::vector<std::stop_token> tokens;
    };

    explicit CancellationScope(std::stop_token token) noexcept;
    /// Install a captured job context, isolating it from this worker's caller.
    explicit CancellationScope(Captured context) noexcept;
    ~CancellationScope();
    CancellationScope(const CancellationScope&) = delete;
    CancellationScope& operator=(const CancellationScope&) = delete;

    static void check();
    [[nodiscard]] static Captured capture();

private:
    CancellationScope* previous;
    std::stop_token token;
    Captured inherited;
    bool boundary {false};
};

}  // namespace Base
