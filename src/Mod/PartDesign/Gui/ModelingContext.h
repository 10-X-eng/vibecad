// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <set>
#include <string>
#include <vector>

#include <App/Application.h>
#include <Mod/PartDesign/PartDesignGlobal.h>

namespace App
{
class Document;
class DocumentObject;
}

namespace PartDesign
{
class Body;
}

namespace PartDesignGui
{

/** Transactional ownership bridge for ordinary Part results.
 *
 * Part commands create their document objects before their task dialogs and
 * grouping code have finished.  This bridge records eligible results at
 * creation time, then moves a complete, validated dependency graph into the
 * Body immediately before the creating transaction closes.  It never moves a
 * graph across an existing GeoFeatureGroup or ordinary group boundary.
 */
class PartDesignGuiExport ModelingContext
{
public:
    static ModelingContext& instance();

    PartDesign::Body*
    adoptPartResult(App::DocumentObject* result, PartDesign::Body* body = nullptr) const;

private:
    ModelingContext();

    struct PendingResult
    {
        App::Document* document;
        long objectId;
        long bodyId;
        int transactionId;
        std::string objectName;
    };

    static bool isOrdinaryPartResult(const App::DocumentObject* object);
    static PartDesign::Body* activeBodyFor(const App::Document* document);
    static bool collectAdoptableGraph(
        PartDesign::Body* body,
        App::DocumentObject* object,
        std::set<App::DocumentObject*>& visited,
        std::vector<App::DocumentObject*>& ordered
    );

    void queueResult(const App::DocumentObject& object);
    void beforeCloseTransaction(bool abort);
    void clearDocument(const App::Document& document);
    void flushPending(const std::set<int>& transactionIds);

    std::vector<PendingResult> pending;
    fastsignals::connection newObjectConnection;
    fastsignals::connection beforeCloseTransactionConnection;
    fastsignals::connection deleteDocumentConnection;
};

}  // namespace PartDesignGui
