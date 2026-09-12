// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2023 Ondsel <development@ondsel.com>                     *
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

#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>
#include <boost/signals2.hpp>

#include <Mod/Assembly/AssemblyGlobal.h>

#include <App/FeaturePython.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>
#include <Base/Placement.h>

#include <OndselSolver/enum.h>

namespace MbD
{
class ASMTPart;
class ASMTAssembly;
class ASMTJoint;
class ASMTMarker;
class ASMTPart;
}  // namespace MbD

namespace App
{
class PropertyXLinkSub;
}  // namespace App

namespace Base
{
class Placement;
class Rotation;
}  // namespace Base


namespace Assembly
{

class AssemblyLink;
class JointGroup;
class ViewGroup;
enum class JointType;


struct ObjRef
{
    App::DocumentObject* obj;
    App::PropertyXLinkSub* ref;
};

class AssemblyExport AssemblyObject: public App::Part
{
    PROPERTY_HEADER_WITH_OVERRIDE(Assembly::AssemblyObject);

public:
    AssemblyObject();
    ~AssemblyObject() override;

    PyObject* getPyObject() override;

    /// returns the type name of the ViewProvider
    const char* getViewProviderName() const override
    {
        return "AssemblyGui::ViewProviderAssembly";
    }

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* prop) override;
    void onSettingDocument() override;
    void unsetupObject() override;
    /* Solve the assembly. It will update first the joints, solve, update placements of the parts
    and redraw the joints Args : enableRedo : This store initial positions to enable undo while
    being in an active transaction (joint creation).*/
    int solve(bool enableRedo = false);
    int generateSimulation(App::DocumentObject* sim);
    /// Snapshot on the document owner, solve detached data on HostRuntime.
    std::uint64_t startSimulation(App::DocumentObject* sim);
    std::uint64_t startSimulationPlayback(App::DocumentObject* sim);
    /// Nonblocking owner-thread adoption; false means the worker is not ready.
    bool finishSimulation(std::uint64_t request = 0);
    /// Invalidate pending work without waiting or retaining the live document.
    void cancelSimulation(std::uint64_t request = 0);
    /// Prepare a frame on HostRuntime; newer requests supersede older requests.
    std::uint64_t requestSimulationFrame(size_t index);
    /// Read one cached pose without scheduling work or changing the document.
    std::vector<std::pair<std::string, Base::Placement>>
    getSimulationFrame(size_t index) const;
    /// Adopt only a ready, still-current frame; never wait on a worker.
    bool finishSimulationFrame(std::uint64_t request);
    /// Consume a ready frame without changing document properties.
    std::optional<std::vector<std::pair<std::string, Base::Placement>>>
    takeSimulationFrame(std::uint64_t request);
    void cancelSimulationFrame(std::uint64_t request = 0);
    /// Scope exact component placement presentation; returns the previous state.
    bool setSimulationPresentation(bool active);
    int updateForFrame(size_t index);
    size_t numberOfFrames();
    void preDrag(std::vector<App::DocumentObject*> dragParts);
    void doDragStep();
    void postDrag();
    void savePlacementsForUndo();
    void undoSolve();
    void clearUndo();

    void exportAsASMT(std::string fileName);

    Base::Placement getMbdPlacement(std::shared_ptr<MbD::ASMTPart> mbdPart);
    bool validateNewPlacements();
    void setNewPlacements();
    static void redrawJointPlacements(std::vector<App::DocumentObject*> joints);
    static void redrawJointPlacement(App::DocumentObject* joint);

    // This makes sure that LinkGroups or sub-assemblies have identity placements.
    void ensureIdentityPlacements();
    // Make sure grounded joints reflect Placement read-only states
    void syncGroundedJoints();

    // Ondsel Solver interface
    std::shared_ptr<MbD::ASMTAssembly> makeMbdAssembly();
    void create_mbdSimulationParameters(App::DocumentObject* sim);
    std::shared_ptr<MbD::ASMTPart> makeMbdPart(
        std::string& name,
        Base::Placement plc = Base::Placement(),
        double mass = 1.0
    );
    std::shared_ptr<MbD::ASMTPart> getMbDPart(App::DocumentObject* obj);
    // To help the solver, during dragging, we are bundling parts connected by a fixed joint.
    // So several assembly components are bundled in a single ASMTPart.
    // So we need to store the plc of each bundled object relative to the bundle origin (first obj
    // of objectPartMap).
    struct MbDPartData
    {
        std::shared_ptr<MbD::ASMTPart> part;
        Base::Placement offsetPlc;  // This is the offset within the bundled parts
    };
    MbDPartData getMbDData(App::DocumentObject* part);
    std::shared_ptr<MbD::ASMTMarker> makeMbdMarker(std::string& name, Base::Placement& plc);
    std::vector<std::shared_ptr<MbD::ASMTJoint>> makeMbdJoint(App::DocumentObject* joint);
    std::shared_ptr<MbD::ASMTJoint> makeMbdJointOfType(App::DocumentObject* joint, JointType jointType);
    std::shared_ptr<MbD::ASMTJoint> makeMbdJointDistance(App::DocumentObject* joint);
    std::string handleOneSideOfJoint(
        App::DocumentObject* joint,
        const char* propRefName,
        const char* propPlcName
    );
    void getRackPinionMarkers(
        App::DocumentObject* joint,
        std::string& markerNameI,
        std::string& markerNameJ
    );
    int slidingPartIndex(App::DocumentObject* joint);

    void jointParts(std::vector<App::DocumentObject*> joints);
    JointGroup* getJointGroup() const;
    ViewGroup* getExplodedViewGroup() const;
    template<typename T>
    T* getGroup();

    std::vector<App::DocumentObject*> getJoints(bool delBadJoints = false, bool subJoints = true);
    std::vector<App::DocumentObject*> getGroundedJoints();
    std::vector<App::DocumentObject*> getJointsOfObj(App::DocumentObject* obj);
    std::vector<App::DocumentObject*> getJointsOfPart(App::DocumentObject* part);
    App::DocumentObject* getJointOfPartConnectingToGround(
        App::DocumentObject* part,
        std::string& name,
        const std::vector<App::DocumentObject*>& excludeJoints = {}
    );
    std::unordered_set<App::DocumentObject*> getGroundedParts();
    std::unordered_set<App::DocumentObject*> fixGroundedParts();
    void fixGroundedPart(App::DocumentObject* obj, Base::Placement& plc, std::string& jointName);

    bool isJointConnectingPartToGround(App::DocumentObject* joint, const char* partPropName);
    bool isJointTypeConnecting(App::DocumentObject* joint);

    bool isObjInSetOfObjRefs(App::DocumentObject* obj, const std::vector<ObjRef>& pairs);
    void removeUnconnectedJoints(
        std::vector<App::DocumentObject*>& joints,
        std::unordered_set<App::DocumentObject*> groundedObjs
    );
    void traverseAndMarkConnectedParts(
        App::DocumentObject* currentPart,
        std::vector<ObjRef>& connectedParts,
        const std::vector<App::DocumentObject*>& joints
    );
    std::vector<ObjRef> getConnectedParts(
        App::DocumentObject* part,
        const std::vector<App::DocumentObject*>& joints
    );
    bool isPartGrounded(App::DocumentObject* part);
    bool isPartConnected(App::DocumentObject* part);

    std::vector<ObjRef> getDownstreamParts(
        App::DocumentObject* part,
        App::DocumentObject* joint = nullptr
    );
    App::DocumentObject* getUpstreamMovingPart(
        App::DocumentObject* part,
        App::DocumentObject*& joint,
        std::string& name,
        std::vector<App::DocumentObject*> excludeJoints = {}
    );

    double getObjMass(App::DocumentObject* obj);
    void setObjMasses(std::vector<std::pair<App::DocumentObject*, double>> objectMasses);

    std::vector<AssemblyLink*> getSubAssemblies();

    std::vector<App::DocumentObject*> getMotionsFromSimulation(App::DocumentObject* sim);

    bool isMbDJointValid(App::DocumentObject* joint);

    bool isEmpty() const;
    int numberOfComponents() const;

    struct SolverConstraintDiagnostic
    {
        std::string specification;
        double residual {0.0};
        bool redundant {false};
    };

    struct SolverJointDiagnostic
    {
        std::string jointName;
        int constraintCount {0};
        int removedDegreesOfFreedom {0};
        int redundantConstraintCount {0};
        double maximumAbsoluteResidual {0.0};
        std::vector<SolverConstraintDiagnostic> constraints;
    };

    void updateSolveStatus();
    inline int getLastDoF() const
    {
        return lastDoF;
    }
    inline bool getLastHasConflicts() const
    {
        return lastHasConflict;
    }
    inline bool getLastHasRedundancies() const
    {
        return lastHasRedundancies;
    }
    inline bool getLastHasPartialRedundancies() const
    {
        return lastHasPartialRedundancies;
    }
    inline bool getLastHasMalformedConstraints() const
    {
        return lastHasMalformedConstraints;
    }
    inline int getLastSolverStatus() const
    {
        return lastSolverStatus;
    }
    inline const std::vector<std::string>& getLastConflicting() const
    {
        return lastConflictingJoints;
    }
    inline const std::vector<std::string>& getLastRedundant() const
    {
        return lastRedundantJoints;
    }
    inline const std::vector<std::string>& getLastPartiallyRedundant() const
    {
        return lastPartialRedundantJoints;
    }
    inline const std::vector<std::string>& getLastMalformed() const
    {
        return lastMalformedJoints;
    }
    inline const std::vector<SolverJointDiagnostic>& getLastJointDiagnostics() const
    {
        return lastJointDiagnostics;
    }
    inline const std::string& getLastSolverMessage() const
    {
        return lastSolverMessage;
    }
    fastsignals::signal<void()> signalSolverUpdate;

private:
    struct SimulationJob;
    std::unique_ptr<SimulationJob> simulationJob;
    std::unique_ptr<SimulationJob> simulationPlayback;
    bool simulationReusePending = false;
    struct SimulationFrameJob;
    std::unique_ptr<SimulationFrameJob> simulationFrameJob;
    std::uint64_t simulationFrameRequest = 0;
    std::uint64_t simulationRequest = 0;
    int prepareSimulation(App::DocumentObject* sim);
    std::uint64_t startSimulationJob(App::DocumentObject* sim, bool allowPlaybackCache);
    bool applySimulationFrame(const std::vector<Base::Placement>& placements);
    void captureTimelineState() noexcept;
    void emitSolverUpdate();
    void requestSolveStatusUpdate();
    void refreshSolveStatus(bool initializeSolver);
    void invalidateConnectivityCache() noexcept;
    void rebuildConnectivityCache();
    void slotConnectivityPropertyChanged(
        const App::DocumentObject& object,
        const App::Property& property
    );
    std::unordered_set<App::DocumentObject*> collectConnectedParts(
        const std::unordered_set<App::DocumentObject*>& roots,
        const std::vector<App::DocumentObject*>& joints
    );

    std::shared_ptr<MbD::ASMTAssembly> mbdAssembly;

    std::unordered_map<App::DocumentObject*, MbDPartData> objectPartMap;
    std::vector<std::pair<App::DocumentObject*, double>> objMasses;
    std::vector<App::DocumentObject*> draggedParts;
    std::vector<App::DocumentObject*> motions;

    std::vector<std::pair<App::DocumentObject*, Base::Placement>> previousPositions;
    long lastTimelinePosition {-1};
    std::vector<App::DocumentObject*> lastTimelineOperations;

    std::unordered_set<App::DocumentObject*> connectedPartsCache;
    std::unordered_set<App::DocumentObject*> groundedPartsCache;
    bool connectivityCacheValid {false};
    fastsignals::scoped_connection connectivityNewObjectConnection;
    fastsignals::scoped_connection connectivityDeletedObjectConnection;
    fastsignals::scoped_connection connectivityChangedObjectConnection;
    fastsignals::scoped_connection connectivityTouchedObjectConnection;
    fastsignals::scoped_connection connectivityRecomputedObjectConnection;
    fastsignals::scoped_connection connectivityPropertyStatusConnection;
    fastsignals::scoped_connection solveStatusStableConnection;
    bool solveStatusUpdatePending {false};

    bool bundleFixed;

    int lastDoF;
    bool lastHasConflict;
    bool lastHasRedundancies;
    bool lastHasPartialRedundancies;
    bool lastHasMalformedConstraints;
    int lastSolverStatus;

    std::vector<std::string> lastRedundantJoints;
    std::vector<std::string> lastConflictingJoints;
    std::vector<std::string> lastPartialRedundantJoints;
    std::vector<std::string> lastMalformedJoints;
    std::vector<SolverJointDiagnostic> lastJointDiagnostics;
    std::string lastSolverMessage;
};

}  // namespace Assembly
