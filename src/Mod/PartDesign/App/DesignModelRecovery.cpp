// SPDX-License-Identifier: LGPL-2.1-or-later

#include "DesignModel.h"

#include <ranges>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Mod/Part/App/PartFeature.h>

#include "Body.h"
#include "DesignFeature.h"

using namespace PartDesign;

namespace
{

std::vector<App::DocumentObject*> recoveryConsumers(
    App::DocumentObject& object
)
{
    std::vector<App::DocumentObject*> consumers;
    for (auto* consumer : object.getInList()) {
        if (consumer
            && !consumer->isDerivedFrom<App::DocumentTimeline>()) {
            consumers.push_back(consumer);
        }
    }
    return consumers;
}

}  // namespace

std::size_t DesignModel::recoverInterruptedOperationPublications(
    App::Document& document
)
{
    auto* timeline = App::DocumentTimeline::get(&document);
    if (!timeline) {
        return 0;
    }

    const auto history = timeline->Operations.getValues();
    std::unordered_map<App::DocumentObject*, std::size_t> historyPositions;
    historyPositions.reserve(history.size());
    for (std::size_t index = 0; index < history.size(); ++index) {
        auto* entry = history[index];
        if (!entry || !historyPositions.emplace(entry, index).second) {
            throw Base::RuntimeError(
                "Global History contains a missing or duplicate object"
            );
        }
    }

    struct OutputRepair
    {
        DesignBodyState* retained {};
        Part::Feature* prior {};
        DesignBodyPublication* publication {};
        Body* body {};
    };

    std::size_t repairedOperations = 0;
    const auto documentObjects = document.getObjects();
    for (auto* operation : documentObjects) {
        if (!operation || !document.containsObject(operation)) {
            continue;
        }
        auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
        if (!properties) {
            continue;
        }
        const auto states = designBodyStatesForOperation(operation);
        const auto outputBodyIds = properties->OutputBodyIds.getValues();
        if (states.size() <= outputBodyIds.size()) {
            continue;
        }

        const auto operationPosition = historyPositions.find(operation);
        if (outputBodyIds.empty() || operationPosition == historyPositions.end()
            || !App::DocumentTimeline::hasTimelineOperationRole(operation)) {
            throw Base::RuntimeError(
                std::string("Interrupted Design operation '")
                + operation->getNameInDocument()
                + "' has duplicate outputs but no exact persisted History root"
            );
        }
        if (document.getBookedTransactionID() == App::NullTransaction
            || document.isPerformingTransaction()
            || !App::GetApplication().transactionIsActive(
                document.getBookedTransactionID()
            )) {
            throw Base::RuntimeError(
                std::string("Interrupted Design operation '")
                + operation->getNameInDocument()
                + "' requires one active caller-owned transaction for recovery"
            );
        }

        std::vector<std::vector<DesignBodyState*>> statesByOutput(
            outputBodyIds.size()
        );
        for (auto* state : states) {
            const int outputIndex = state ? state->OutputIndex.getValue() : -1;
            if (!state || outputIndex < 0
                || static_cast<std::size_t>(outputIndex) >= outputBodyIds.size()
                || state->BodyId.getValueStr()
                    != outputBodyIds[static_cast<std::size_t>(outputIndex)]
                || state->Operation.getValue() != operation
                || !App::DocumentTimeline::isTimelineResourceOwnedBy(
                    state,
                    operation
                )) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' has duplicate outputs without exact Body, slot, and owner identity"
                );
            }
            statesByOutput[static_cast<std::size_t>(outputIndex)].push_back(state);
        }

        auto repairedInputs = properties->InputStates.getValues();
        const auto inputBodyIds = properties->InputBodyIds.getValues();
        const auto priorMappings = properties->OutputPreviousInputIndices.getValues();
        if (repairedInputs.size() != inputBodyIds.size()
            || priorMappings.size() != outputBodyIds.size()) {
            throw Base::RuntimeError(
                std::string("Interrupted Design operation '")
                + operation->getNameInDocument()
                + "' has inconsistent saved input and output ports"
            );
        }

        std::vector<OutputRepair> outputRepairs;
        std::unordered_set<DesignBodyState*> allOwnedStates(states.begin(), states.end());
        std::unordered_set<DesignBodyState*> allDuplicates;
        bool consumedOwnOutput = false;
        for (std::size_t outputIndex = 0;
             outputIndex < statesByOutput.size();
             ++outputIndex) {
            auto& outputStates = statesByOutput[outputIndex];
            if (outputStates.empty()) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' is missing output slot " + std::to_string(outputIndex)
                );
            }
            if (outputStates.size() == 1) {
                const auto position = historyPositions.find(
                    outputStates.front()
                );
                if (position == historyPositions.end()
                    || position->second >= operationPosition->second) {
                    throw Base::RuntimeError(
                        std::string("Interrupted Design operation '")
                        + operation->getNameInDocument()
                        + "' has a noncanonical retained output for slot "
                        + std::to_string(outputIndex)
                    );
                }
                continue;
            }

            DesignBodyState* retained = nullptr;
            for (auto* state : outputStates) {
                const auto position = historyPositions.find(state);
                if (position != historyPositions.end()
                    && position->second < operationPosition->second) {
                    if (retained) {
                        throw Base::RuntimeError(
                            std::string("Interrupted Design operation '")
                            + operation->getNameInDocument()
                            + "' has more than one persisted output for slot "
                            + std::to_string(outputIndex)
                        );
                    }
                    retained = state;
                }
            }
            if (!retained) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' has no persisted output before its History root for slot "
                    + std::to_string(outputIndex)
                );
            }

            std::unordered_set<DesignBodyState*> chain;
            Part::Feature* cursor = retained;
            while (auto* owned = freecad_cast<DesignBodyState*>(cursor)) {
                if (!allOwnedStates.contains(owned)
                    || owned->BodyId.getValueStr() != outputBodyIds[outputIndex]) {
                    break;
                }
                if (!chain.insert(owned).second) {
                    throw Base::RuntimeError(
                        std::string("Interrupted Design operation '")
                        + operation->getNameInDocument()
                        + "' has a cyclic duplicate output chain for slot "
                        + std::to_string(outputIndex)
                    );
                }
                cursor = freecad_cast<Part::Feature*>(
                    owned->PreviousState.getValue()
                );
            }
            if (chain.size() != outputStates.size() || !cursor) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' does not have one exact duplicate chain back to a prior Body state"
                );
            }

            const long inputIndex = priorMappings[outputIndex];
            if (inputIndex < 0
                || static_cast<std::size_t>(inputIndex) >= repairedInputs.size()
                || inputBodyIds[static_cast<std::size_t>(inputIndex)]
                    != outputBodyIds[outputIndex]) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' has no exact prior-state input mapping for its duplicate output"
                );
            }
            auto* selfInput = freecad_cast<DesignBodyState*>(
                repairedInputs[static_cast<std::size_t>(inputIndex)]
            );
            if (!selfInput || !chain.contains(selfInput)) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' has duplicate outputs but does not consume that interrupted chain"
                );
            }
            repairedInputs[static_cast<std::size_t>(inputIndex)] = cursor;
            consumedOwnOutput = true;

            auto* body = bodyWithId(document, outputBodyIds[outputIndex]);
            auto* publication = findDesignBodyPublication(body);
            const auto* priorState = freecad_cast<DesignBodyState*>(cursor);
            const bool priorBelongsToBody = body
                && ((priorState
                     && priorState->BodyId.getValueStr()
                         == outputBodyIds[outputIndex])
                    || (!priorState && body->hasObject(cursor)));
            if (!body || !priorBelongsToBody || !publication
                || body->Tip.getValue() != publication
                || !chain.contains(freecad_cast<DesignBodyState*>(
                    publication->CurrentState.getValue()
                ))) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' has no exact prior state and published Body tip for its duplicate output"
                );
            }

            OutputRepair repair {
                .retained = retained,
                .prior = cursor,
                .publication = publication,
                .body = body,
            };
            for (auto* state : outputStates) {
                if (state == retained) {
                    continue;
                }
                const auto position = historyPositions.find(state);
                if (position == historyPositions.end()
                    || position->second <= operationPosition->second) {
                    throw Base::RuntimeError(
                        std::string("Interrupted Design operation '")
                        + operation->getNameInDocument()
                        + "' has a duplicate output inside its accepted History block"
                    );
                }
                allDuplicates.insert(state);
            }
            outputRepairs.push_back(std::move(repair));
        }

        if (!consumedOwnOutput || outputRepairs.empty()) {
            throw Base::RuntimeError(
                std::string("Design operation '") + operation->getNameInDocument()
                + "' has excess output resources that are not an interrupted publication retry"
            );
        }
        for (auto* duplicate : allDuplicates) {
            for (auto* consumer : recoveryConsumers(*duplicate)) {
                if (consumer == operation
                    || allOwnedStates.contains(
                        freecad_cast<DesignBodyState*>(consumer)
                    )) {
                    continue;
                }
                if (std::ranges::any_of(
                        outputRepairs,
                        [consumer](const OutputRepair& repair) {
                            return consumer == repair.publication;
                        }
                    )) {
                    continue;
                }
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' has a duplicate output used by another document object"
                );
            }
        }

        properties->InputStates.setValues(repairedInputs);
        for (const auto& repair : outputRepairs) {
            repair.retained->PreviousState.setValue(repair.prior);
            repair.publication->CurrentState.setValue(repair.retained);
        }
        std::vector<std::string> duplicateNames;
        duplicateNames.reserve(allDuplicates.size());
        for (auto* historyObject : history) {
            if (auto* state = freecad_cast<DesignBodyState*>(historyObject);
                state && allDuplicates.contains(state)) {
                duplicateNames.emplace_back(state->getNameInDocument());
            }
        }
        if (duplicateNames.size() != allDuplicates.size()) {
            throw Base::RuntimeError(
                std::string("Interrupted Design operation '")
                + operation->getNameInDocument()
                + "' lost a duplicate output before deterministic removal"
            );
        }
        for (const auto& name : duplicateNames) {
            document.removeObject(name.c_str());
        }

        document.recomputeFeature(operation, true);
        if (!operation->isValid()) {
            throw Base::RuntimeError(
                std::string("Interrupted Design operation '")
                + operation->getNameInDocument()
                + "' recovered its exact prior Body state, but its geometry remains invalid: "
                + operation->getStatusString()
            );
        }
        for (const auto& repair : outputRepairs) {
            repair.retained->Operation.touch();
            document.recomputeFeature(repair.retained, true);
            if (!repair.retained->isValid()) {
                throw Base::RuntimeError(
                    std::string("Interrupted Design operation '")
                    + operation->getNameInDocument()
                    + "' recovered, but its retained Body state did not republish: "
                    + repair.retained->getStatusString()
                );
            }
            repair.publication->CurrentState.touch();
            repair.body->Tip.touch();
        }
        ++repairedOperations;
    }

    return repairedOperations;
}
