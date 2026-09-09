// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <functional>
#include <limits>
#include <queue>
#include <stdexcept>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace App::detail
{
// One call-scoped ownership census can validate many publication blocks. Do
// not retain it across document changes: ownership is part of the live contract.
template<typename Node, typename ResolveRoot>
auto countSemanticMembers(const std::vector<Node>& members, ResolveRoot resolveRoot)
{
    using Root = std::decay_t<decltype(resolveRoot(std::declval<Node>()))>;
    std::unordered_map<Root, std::size_t> counts;
    counts.reserve(members.size());
    for (const auto member : members) {
        ++counts[resolveRoot(member)];
    }
    return counts;
}

// Value-only graph: safe to prepare/consume without document or GUI pointers.
struct SemanticDependencyGraph
{
    static constexpr std::size_t untracked = std::numeric_limits<std::size_t>::max();
    std::vector<std::vector<std::size_t>> members;
    std::vector<std::vector<std::size_t>> dependencies;
    std::vector<std::size_t> block;
};

struct SemanticDependencyOrder
{
    std::vector<std::size_t> order;
    std::size_t visitedEdges = 0;
};

inline SemanticDependencyOrder stableSemanticDependencyOrder(const SemanticDependencyGraph& graph)
{
    if (graph.block.size() != graph.dependencies.size())
        throw std::runtime_error("received mismatched graph state");
    std::vector<bool> enrolled(graph.block.size(), false);
    for (std::size_t block = 0; block < graph.members.size(); ++block) {
        for (const auto node : graph.members[block]) {
            if (node >= graph.block.size() || graph.block[node] != block || enrolled[node])
                throw std::runtime_error("received malformed block membership");
            enrolled[node] = true;
        }
    }
    std::vector<std::vector<std::size_t>> consumers(graph.members.size());
    std::vector<std::size_t> indegree(graph.members.size(), 0);
    std::vector<std::size_t> visited(graph.block.size(), SemanticDependencyGraph::untracked);
    SemanticDependencyOrder result;
    for (std::size_t block = 0; block < graph.members.size(); ++block) {
        auto pending = graph.members[block];
        for (const auto node : pending) visited[node] = block;
        std::unordered_set<std::size_t> inputs;
        while (!pending.empty()) {
            const auto node = pending.back();
            pending.pop_back();
            for (const auto dependency : graph.dependencies[node]) {
                ++result.visitedEdges;
                if (dependency >= graph.block.size())
                    throw std::runtime_error("received an invalid dependency index");
                if (visited[dependency] == block) continue;
                visited[dependency] = block;
                const auto owner = graph.block[dependency];
                if (owner != SemanticDependencyGraph::untracked && owner != block) {
                    if (owner >= graph.members.size())
                        throw std::runtime_error("received an invalid semantic root");
                    if (inputs.insert(owner).second) {
                        consumers[owner].push_back(block);
                        ++indegree[block];
                    }
                    // Every enrolled block is processed independently. Its
                    // transitive dependencies do not need duplicate edges here.
                    if (enrolled[dependency]) continue;
                }
                pending.push_back(dependency);
            }
        }
    }
    std::priority_queue<std::size_t, std::vector<std::size_t>, std::greater<>> ready;
    for (std::size_t block = 0; block < indegree.size(); ++block)
        if (indegree[block] == 0) ready.push(block);
    result.order.reserve(indegree.size());
    while (!ready.empty()) {
        const auto next = ready.top();
        ready.pop();
        result.order.push_back(next);
        for (const auto consumer : consumers[next])
            if (--indegree[consumer] == 0) ready.push(consumer);
    }
    if (result.order.size() != indegree.size())
        throw std::runtime_error("detected a cycle");
    return result;
}
} // namespace App::detail
