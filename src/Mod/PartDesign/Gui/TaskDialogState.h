// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <algorithm>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/Macro.h>
#include <Gui/ViewProviderDocumentObject.h>

namespace PartDesignGui::TaskInternal
{

/**
 * Captures the exact visibility state at task entry.
 *
 * Native task panels temporarily reveal profiles, paths, operands, and
 * previews.  Transaction rollback is responsible for model properties, but
 * those temporary GUI changes must also be restored when an existing feature
 * survives Cancel.
 */
class VisibilitySnapshot
{
public:
    VisibilitySnapshot() = default;

    explicit VisibilitySnapshot(App::Document* document)
    {
        capture(document);
    }

    void capture(App::Document* document)
    {
        states.clear();
        if (!document) {
            return;
        }
        states.reserve(document->getObjects().size());
        for (auto* object : document->getObjects()) {
            captureObject(object);
        }
    }

    void captureObject(App::DocumentObject* object)
    {
        if (!object || !object->getNameInDocument()) {
            return;
        }
        const auto duplicate = std::ranges::find(
            states,
            object->getID(),
            &State::id
        );
        if (duplicate != states.end()) {
            return;
        }
        auto* viewProvider = Gui::Application::Instance
            ? Gui::Application::Instance
                  ->getViewProvider<Gui::ViewProviderDocumentObject>(object)
            : nullptr;
        states.push_back(
            {
                object->getID(),
                object->getNameInDocument(),
                object->Visibility.getValue(),
                viewProvider ? viewProvider->Visibility.getValue() : false,
                viewProvider != nullptr,
            }
        );
    }

    void restore(App::Document* document) const noexcept
    {
        if (!document || !Gui::Application::Instance) {
            return;
        }
        for (const auto& state : states) {
            try {
                auto* object = document->getObjectByID(state.id);
                if (!object || !object->getNameInDocument()
                    || state.name != object->getNameInDocument()) {
                    continue;
                }
                object->Visibility.setValue(state.objectVisible);
                auto* viewProvider =
                    Gui::Application::Instance
                        ->getViewProvider<Gui::ViewProviderDocumentObject>(
                            object
                        );
                if (state.hasViewProvider && viewProvider) {
                    viewProvider->Visibility.setValue(state.viewVisible);
                }
            }
            catch (const Base::Exception& error) {
                Base::Console().error(
                    "Could not restore native task visibility for '%s': %s\n",
                    state.name.c_str(),
                    error.what()
                );
            }
            catch (...) {
                Base::Console().error(
                    "Could not restore native task visibility for '%s'.\n",
                    state.name.c_str()
                );
            }
        }
    }

private:
    struct State
    {
        long id;
        std::string name;
        bool objectVisible;
        bool viewVisible;
        bool hasViewProvider;
    };

    std::vector<State> states;
};

/**
 * Ends a task edit through the transaction ownership recorded by Gui::Document.
 *
 * If edit mode has already ended, the common TaskDialog checkpoint remains
 * responsible for its exact transaction ID. Never abort whichever unrelated
 * transaction happens to be current merely because the panel is still open.
 */
inline void cancelOwnedEdit(Gui::Document* guiDocument)
{
    if (guiDocument && guiDocument->getEditViewProvider()) {
        guiDocument->cancelEdit();
    }
}

/**
 * Records Python-console/macro lines only after a native task is accepted.
 *
 * The commands still execute immediately against the live preview.  Failed
 * Apply/OK attempts discard their trace so replay never contains geometry
 * which was rejected by validation.
 */
class AcceptedMacro
{
public:
    AcceptedMacro()
    {
        if (!Gui::Application::Instance
            || !Gui::Application::Instance->macroManager()) {
            return;
        }
        redirector = std::make_unique<Gui::MacroManager::MacroRedirector>(
            [this](Gui::MacroManager::LineType type, const char* line) {
                if (line) {
                    lines.emplace_back(type, line);
                }
            }
        );
    }

    AcceptedMacro(const AcceptedMacro&) = delete;
    AcceptedMacro& operator=(const AcceptedMacro&) = delete;

    ~AcceptedMacro()
    {
        discard();
    }

    void discard() noexcept
    {
        redirector.reset();
        lines.clear();
    }

    void publish() noexcept
    {
        auto acceptedLines = std::move(lines);
        redirector.reset();
        if (!Gui::Application::Instance) {
            return;
        }
        try {
            auto* manager = Gui::Application::Instance->macroManager();
            if (!manager) {
                return;
            }
            for (const auto& [type, line] : acceptedLines) {
                manager->addLine(type, line.c_str());
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().warning(
                "Native result was accepted, but its macro record failed: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().warning(
                "Native result was accepted, but its macro record failed.\n"
            );
        }
    }

private:
    std::vector<std::pair<Gui::MacroManager::LineType, std::string>> lines;
    std::unique_ptr<Gui::MacroManager::MacroRedirector> redirector;
};

}  // namespace PartDesignGui::TaskInternal
