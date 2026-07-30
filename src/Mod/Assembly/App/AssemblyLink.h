// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/


#pragma once

#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <Mod/Assembly/AssemblyGlobal.h>

#include <App/FeaturePython.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>


namespace Assembly
{
class AssemblyObject;
class JointGroup;

struct AssemblyExport AssemblyLinkResourceIdentity
{
    long objectId {-1};
    std::string objectName;
};

struct AssemblyExport AssemblyLinkSynchronizationResult
{
    std::vector<AssemblyLinkResourceIdentity> orderedOldResourceIdentities;
    std::vector<App::DocumentObject*> oldToFinalResources;
    std::vector<App::DocumentObject*> orderedFinalResources;
    std::vector<AssemblyLinkResourceIdentity> retiredResourceIdentities;
};

class AssemblyExport AssemblyLink: public App::Part
{
    PROPERTY_HEADER_WITH_OVERRIDE(Assembly::AssemblyLink);

public:
    static constexpr const char* SourceDocumentPropertyName =
        "VibeCADAssemblySourceDocument";
    static constexpr const char* SourceObjectIdPropertyName =
        "VibeCADAssemblySourceObjectId";
    static constexpr const char* SourceObjectNamePropertyName =
        "VibeCADAssemblySourceObjectName";

    AssemblyLink();
    ~AssemblyLink() override;

    /// Install the application transaction hook which keeps published
    /// occurrences synchronized with committed source-assembly structure.
    static void installTransactionSynchronization();

    PyObject* getPyObject() override;

    /// returns the type name of the ViewProvider
    const char* getViewProviderName() const override
    {
        return "AssemblyGui::ViewProviderAssemblyLink";
    }

    App::DocumentObjectExecReturn* execute() override;

    // The linked assembly is the AssemblyObject that this AssemblyLink pseudo-links to recursively.
    AssemblyObject* getLinkedAssembly() const;
    // The parent assembly is the main assembly in which the linked assembly is contained
    AssemblyObject* getParentAssembly() const;

    // Overriding DocumentObject::getLinkedObject is giving bugs
    // This function returns the linked object, either an AssemblyObject or an AssemblyLink
    App::DocumentObject* getLinkedObject2(bool recurse = true) const;

    bool isRigid() const;

    /**
     * Update all of the components and joints from the Assembly
     */
    void updateContents();
    AssemblyLinkSynchronizationResult synchronizeContentsWithResourceMap(
        const std::vector<App::DocumentObject*>& orderedOldResources
    );
    void updateParentJoints();

    void synchronizeComponents();
    void synchronizeJoints();
    void handleJointReference(
        App::DocumentObject* joint,
        App::DocumentObject* lJoint,
        const char* refName
    );
    void ensureNoJointGroup();
    JointGroup* ensureJointGroup();
    std::vector<App::DocumentObject*> getJoints();

    bool allowDuplicateLabel() const override;

    bool isEmpty() const;
    int numberOfComponents() const;

    App::PropertyXLink LinkedObject;
    App::PropertyBool Rigid;

    std::unordered_map<App::DocumentObject*, App::DocumentObject*> objLinkMap;

protected:
    void onBeforeChange(const App::Property* prop) override;
    /// get called by the container whenever a property has been changed
    void onChanged(const App::Property* prop) override;

private:
    static void synchronizeTransactionBeforeClose(
        int transactionId,
        bool aborted,
        const std::vector<App::Document*>& participatingDocuments
    );
    void rebaseAfterSameDocumentSources();
    void refreshContentsDuringExecution();
    void updateContentsUnchecked();
    bool hasStructuralContentDiff() const;
    AssemblyLinkSynchronizationResult
    synchronizeContentsWithResourceMapUnchecked(
        const std::vector<App::DocumentObject*>& orderedOldResources
    );
    void recordResourceReplacement(
        App::DocumentObject* oldResource,
        App::DocumentObject* finalResource
    );
    void recordResourceRetirement(App::DocumentObject* oldResource);

    std::unordered_map<
        const App::DocumentObject*,
        App::DocumentObject*
    >* _resourceReplacementTrace {nullptr};
    std::vector<App::DocumentObject*>* _resourceRetirementTrace {
        nullptr
    };
    bool _resourceReconciliationActive {false};
    bool _valueRefreshOnly {false};
};


}  // namespace Assembly
