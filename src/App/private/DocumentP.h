// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#pragma once

#ifdef _MSC_VER
#pragma warning(disable : 4834)
#endif

#include <map>
#include <string>
#include <memory>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <optional>

#include <boost/bimap.hpp>
#include <boost/graph/adjacency_list.hpp>

#include <CXX/Objects.hxx>

#include <App/DocumentObject.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <App/StringHasher.h>
#include <App/ExportInfo.h>
#include <Base/UniqueNameManager.h>

// using VertexProperty = boost::property<boost::vertex_root_t, DocumentObject* >;
using DependencyList = boost::adjacency_list<
    boost::vecS,         // class OutEdgeListS  : a Sequence or an AssociativeContainer
    boost::vecS,         // class VertexListS   : a Sequence or a RandomAccessContainer
    boost::directedS,    // class DirectedS     : This is a directed graph
    boost::no_property,  // class VertexProperty:
    boost::no_property,  // class EdgeProperty:
    boost::no_property,  // class GraphProperty:
    boost::listS         // class EdgeListS:
    >;
using Traits = boost::graph_traits<DependencyList>;
using Vertex = Traits::vertex_descriptor;
using Edge = Traits::edge_descriptor;
using Node = std::vector<size_t>;
using Path = std::vector<size_t>;

namespace App
{
using HasherMap = boost::bimap<StringHasherRef, int>;
class Transaction;

// Pimpl class
struct DocumentP
{
    // Array to preserve the creation order of created objects
    std::vector<DocumentObject*> objectArray;
    std::unordered_set<App::DocumentObject*> touchedObjs;
    std::unordered_map<std::string, DocumentObject*> objectMap;
    Base::UniqueNameManager objectNameManager;
    Base::UniqueNameManager objectLabelManager;
    std::unordered_map<long, DocumentObject*> objectIdMap;
    std::unordered_map<std::string, bool> partialLoadObjects;
    std::vector<DocumentObjectT> pendingRemove;
    long lastObjectId {};
    DocumentObject* activeObject {nullptr};
    Transaction* activeUndoTransaction {nullptr};
    // pointer to the python class
    Py::Object DocumentPythonObject;
    int iTransactionMode {0};
    bool rollback {false};
    bool undoing {false};  ///< document in the middle of undo or redo
    bool committing {false};
    bool opentransaction {false};
    unsigned int addObjectCriticalDepth {0};
    unsigned int breakDependencyDepth {0};
    unsigned int timelineCopyImportDepth {0};
    std::bitset<32> StatusBits;
    int iUndoMode {0};
    unsigned int UndoMemSize {0};
    unsigned int UndoMaxStackSize {20};
    unsigned int TransactionLock {0};
    // Id and name that the next transaction will take
    // as soon as there is a change to the document
    int bookedTransaction { 0 }; 

    std::string programVersion;
    mutable HasherMap hashers;
    std::multimap<const App::DocumentObject*, std::unique_ptr<App::DocumentObjectExecReturn>>
        _RecomputeLog;
    std::uint64_t recomputeDiagnosticGeneration {0};
    std::vector<RecomputeDiagnostic> recomputeDiagnostics;
    ExportInfo exportInfo;

    StringHasherRef Hasher {new StringHasher};

    DocumentP();

    void beginRecomputeDiagnostics()
    {
        ++recomputeDiagnosticGeneration;
        recomputeDiagnostics.clear();
    }

    void addRecomputeDiagnostic(const std::string& why,
                                App::DocumentObject* obj,
                                std::string_view code,
                                std::string_view algorithm = {},
                                std::string_view property = {},
                                std::string_view subelement = {})
    {
        RecomputeDiagnostic diagnostic;
        diagnostic.generation = recomputeDiagnosticGeneration;
        diagnostic.severity = "error";
        diagnostic.code = std::string(code);
        diagnostic.object = obj && obj->getNameInDocument() ? obj->getNameInDocument() : "";
        diagnostic.algorithm = algorithm.empty() && obj
            ? std::string(obj->getTypeId().getName())
            : std::string(algorithm);
        diagnostic.property = std::string(property);
        diagnostic.subelement = std::string(subelement);
        diagnostic.message = why;
        recomputeDiagnostics.push_back(std::move(diagnostic));
    }

    void addRecomputeLog(const char* why,
                         App::DocumentObject* obj,
                         std::string_view code = "FEATURE_EXECUTION_FAILED",
                         std::string_view algorithm = {},
                         std::string_view property = {},
                         std::string_view subelement = {})
    {
        addRecomputeLog(
            new DocumentObjectExecReturn(why, obj),
            code,
            algorithm,
            property,
            subelement
        );
    }

    void addRecomputeLog(const std::string& why,
                         App::DocumentObject* obj,
                         std::string_view code = "FEATURE_EXECUTION_FAILED",
                         std::string_view algorithm = {},
                         std::string_view property = {},
                         std::string_view subelement = {})
    {
        addRecomputeLog(
            new DocumentObjectExecReturn(why, obj),
            code,
            algorithm,
            property,
            subelement
        );
    }

    void addRecomputeLog(DocumentObjectExecReturn* returnCode,
                         std::string_view code = "FEATURE_EXECUTION_FAILED",
                         std::string_view algorithm = {},
                         std::string_view property = {},
                         std::string_view subelement = {})
    {
        if (!returnCode->Which) {
            delete returnCode;
            return;
        }
        addRecomputeDiagnostic(
            returnCode->Why,
            returnCode->Which,
            code,
            algorithm,
            property,
            subelement
        );
        _RecomputeLog.emplace(returnCode->Which,
                              std::unique_ptr<DocumentObjectExecReturn>(returnCode));
        returnCode->Which->setStatus(ObjectStatus::Error, true);
    }

    void clearRecomputeLog(const App::DocumentObject* obj = nullptr)
    {
        if (!obj) {
            _RecomputeLog.clear();
        }
        else {
            _RecomputeLog.erase(obj);
        }
    }

    void clearDocument()
    {
        objectLabelManager.clear();
        objectArray.clear();
        for (auto& v : objectMap) {
            v.second->setStatus(ObjectStatus::Destroy, true);
            delete (v.second);
            v.second = nullptr;
        }
        objectMap.clear();
        objectNameManager.clear();
        objectIdMap.clear();
    }

    const char* findRecomputeLog(const App::DocumentObject* obj)
    {
        auto range = _RecomputeLog.equal_range(obj);
        if (range.first == range.second) {
            return nullptr;
        }
        return (--range.second)->second->Why.c_str();
    }

    static void findAllPathsAt(const std::vector<Node>& all_nodes,
                               size_t id,
                               std::vector<Path>& all_paths,
                               Path tmp);
    std::vector<App::DocumentObject*>
    topologicalSort(const std::vector<App::DocumentObject*>& objects) const;
    static std::vector<App::DocumentObject*>
    partialTopologicalSort(const std::vector<App::DocumentObject*>& objects);
    static void checkStringHasher(const Base::XMLReader& reader);
};

}  // namespace App
