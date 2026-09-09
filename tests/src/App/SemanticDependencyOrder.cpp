// SPDX-License-Identifier: LGPL-2.1-or-later
#include <gtest/gtest.h>
#include <App/SemanticDependencyOrder.h>
#include <algorithm>
#include <numeric>
#include <random>
#include <set>

namespace {
using App::detail::SemanticDependencyGraph;
using App::detail::stableSemanticDependencyOrder;

std::vector<std::size_t> referenceOrder(const SemanticDependencyGraph& graph)
{
    std::vector<std::set<std::size_t>> dependencies(graph.members.size());
    for (std::size_t block = 0; block < graph.members.size(); ++block) {
        std::set<std::size_t> visited;
        auto pending = graph.members[block];
        while (!pending.empty()) {
            const auto node = pending.back();
            pending.pop_back();
            if (!visited.insert(node).second) continue;
            const auto owner = graph.block[node];
            if (owner != SemanticDependencyGraph::untracked && owner != block)
                dependencies[block].insert(owner);
            pending.insert(pending.end(), graph.dependencies[node].begin(), graph.dependencies[node].end());
        }
    }
    std::vector<std::size_t> result;
    while (result.size() < dependencies.size()) {
        std::size_t next = 0;
        for (; next < dependencies.size(); ++next) {
            if (dependencies[next].empty()
                && std::find(result.begin(), result.end(), next) == result.end()) break;
        }
        if (next == dependencies.size()) throw std::runtime_error("cycle");
        result.push_back(next);
        for (auto& inputs : dependencies) inputs.erase(next);
    }
    return result;
}
}

TEST(SemanticDependencyOrder, PreservesLexicographicOrderAgainstFullReachability)
{
    std::mt19937 random(914);
    for (int trial = 0; trial < 100; ++trial) {
        SemanticDependencyGraph graph;
        graph.members.resize(12);
        graph.dependencies.resize(30);
        graph.block.assign(30, SemanticDependencyGraph::untracked);
        for (std::size_t index = 0; index < 12; ++index) {
            graph.members[index] = {index};
            graph.block[index] = index;
        }
        // Include shared untracked nodes and non-topologically numbered roots.
        std::vector<std::size_t> order(30);
        std::iota(order.begin(), order.end(), 0);
        std::shuffle(order.begin(), order.end(), random);
        for (std::size_t index = 1; index < order.size(); ++index)
            for (std::size_t prior = 0; prior < index; ++prior)
                if (random() % 7 == 0) graph.dependencies[order[index]].push_back(order[prior]);
        EXPECT_EQ(stableSemanticDependencyOrder(graph).order, referenceOrder(graph));
    }
}

TEST(SemanticDependencyOrder, TraversesChainOncePerDirectSemanticDependency)
{
    SemanticDependencyGraph graph;
    constexpr std::size_t count = 10000;
    graph.members.resize(count);
    graph.dependencies.resize(count);
    graph.block.resize(count);
    for (std::size_t index = 0; index < count; ++index) {
        graph.members[index] = {index};
        graph.block[index] = index;
        if (index) graph.dependencies[index] = {index - 1};
    }
    const auto result = stableSemanticDependencyOrder(graph);
    ASSERT_EQ(result.order.size(), count);
    EXPECT_EQ(result.visitedEdges, count - 1);
    for (std::size_t index = 0; index < count; ++index) EXPECT_EQ(result.order[index], index);
}

TEST(SemanticDependencyOrder, PreservesCyclesAndUntrackedResourceDependencies)
{
    SemanticDependencyGraph graph {{{0}, {1}}, {{2}, {}, {1}}, {0, 1, 1}};
    // Node 2 belongs to block 1 but is not enrolled: its links still count.
    EXPECT_EQ(stableSemanticDependencyOrder(graph).order, referenceOrder(graph));
    graph.dependencies[1] = {0};
    EXPECT_THROW(stableSemanticDependencyOrder(graph), std::runtime_error);
    graph = {{{0, 1}}, {{1}, {0}}, {0, 0}};
    EXPECT_EQ(stableSemanticDependencyOrder(graph).order, (std::vector<std::size_t>{0}));
}

TEST(SemanticDependencyOrder, CountsSharedPublicationMembershipInOnePass)
{
    std::vector<std::size_t> members(10000);
    std::iota(members.begin(), members.end(), 0);
    std::size_t resolutions = 0;
    const auto counts = App::detail::countSemanticMembers(members, [&](std::size_t member) {
        ++resolutions;
        return member / 10;
    });
    ASSERT_EQ(counts.size(), 1000);
    for (std::size_t publication = 0; publication < 1000; ++publication) {
        EXPECT_EQ(counts.at(publication), 10);
    }
    EXPECT_EQ(resolutions, members.size());
    // A fresh pass must see changed ownership and count duplicate entries,
    // rather than caching a previous document state or hiding malformed input.
    members.back() = 0;
    const auto changed = App::detail::countSemanticMembers(members, [](std::size_t member) {
        return member / 10;
    });
    EXPECT_EQ(changed.at(0), 11);
    EXPECT_EQ(changed.at(999), 9);
}
