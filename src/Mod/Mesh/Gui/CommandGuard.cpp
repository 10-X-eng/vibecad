// SPDX-License-Identifier: LGPL-2.1-or-later

#include "CommandGuard.h"

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <Gui/Control.h>

namespace MeshGui
{
bool hasCleanNativeMutationBoundary(const App::Document* document)
{
    return document && document->getBookedTransactionID() == App::NullTransaction
        && !document->hasPendingTransaction() && !document->isTransactionLocked()
        && !document->transacting();
}

bool canStartNativeMeshCommand(const App::Document* document)
{
    return hasCleanNativeMutationBoundary(document) && !Gui::Control().activeDialog();
}

bool isNativeMeshInputActive(const App::DocumentObject* object) noexcept
{
    try {
        if (!App::DocumentTimeline::isObjectUsableAtCurrentPosition(object)) {
            return false;
        }

        const auto* linked = object->getLinkedObject(true);
        return !linked || linked == object
            || App::DocumentTimeline::isObjectUsableAtCurrentPosition(linked);
    }
    catch (...) {
        return false;
    }
}

}  // namespace MeshGui
