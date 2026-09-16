// SPDX-License-Identifier: LGPL-2.1-or-later

#include "CancellationScope.h"
#include "Exception.h"
#include <algorithm>
#include <utility>

namespace Base
{

namespace
{
// Keep TLS inside FreeCADBase rather than exporting a TLS data member, which
// MSVC does not support. Every module accesses it through the exported methods.
thread_local CancellationScope* current = nullptr;
}

CancellationScope::CancellationScope(std::stop_token token) noexcept
    : previous(current), token(std::move(token))
{
    current = this;
}

CancellationScope::~CancellationScope()
{
    current = previous;
}

CancellationScope::CancellationScope(Captured context) noexcept
    : previous(current), inherited(std::move(context)), boundary(true)
{
    current = this;
}

void CancellationScope::check()
{
    for (auto* scope = current; scope; scope = scope->previous) {
        if (scope->token.stop_requested()) {
            throw AbortException();
        }
        for (const auto& token : scope->inherited.tokens) {
            if (token.stop_requested()) {
                throw AbortException();
            }
        }
        if (scope->boundary) {
            break;
        }
    }
}

CancellationScope::Captured CancellationScope::capture()
{
    Captured result;
    const auto append = [&result](const std::stop_token& token) {
        if (token.stop_possible()
            && std::find(result.tokens.begin(), result.tokens.end(), token) == result.tokens.end()) {
            result.tokens.push_back(token);
        }
    };
    for (auto* scope = current; scope; scope = scope->previous) {
        append(scope->token);
        for (const auto& token : scope->inherited.tokens) {
            append(token);
        }
        if (scope->boundary) {
            break;
        }
    }
    return result;
}

}  // namespace Base
