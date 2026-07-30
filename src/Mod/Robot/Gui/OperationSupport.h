// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <vector>

#include <Mod/Robot/RobotGlobal.h>

namespace App
{
class Document;
class DocumentObject;
}  // namespace App

namespace Gui
{
class ExactTransaction;
}

namespace Robot
{
class RobotObject;
class TrajectoryObject;
}  // namespace Robot

namespace RobotGui::OperationSupport
{

struct RobotTrajectorySelection
{
    App::Document* activeDocument {nullptr};
    Robot::RobotObject* robot {nullptr};
    Robot::TrajectoryObject* trajectory {nullptr};

    explicit operator bool() const noexcept
    {
        return activeDocument && robot && trajectory;
    }
};

RobotGuiExport bool hasCleanBoundary(const App::Document* document) noexcept;

RobotGuiExport App::Document* cleanActiveDocument() noexcept;

RobotGuiExport bool isUsableObject(const App::DocumentObject* object) noexcept;

RobotGuiExport Robot::RobotObject* selectedRobot() noexcept;

RobotGuiExport Robot::TrajectoryObject* selectedTrajectory() noexcept;

RobotGuiExport RobotTrajectorySelection selectedRobotAndTrajectory() noexcept;

RobotGuiExport std::vector<Robot::TrajectoryObject*> selectedTrajectories();

RobotGuiExport App::DocumentObject* selectedToolShape(const Robot::RobotObject& robot) noexcept;

RobotGuiExport std::vector<App::Document*> mutationDocuments(
    App::Document& activeDocument,
    const std::vector<App::DocumentObject*>& mutatedObjects
);

RobotGuiExport void requireCleanDocuments(
    App::Document& activeDocument,
    const std::vector<App::Document*>& documents
);

RobotGuiExport void publishOperation(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& resources = {}
);

RobotGuiExport void publishReplacingOperation(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& replacedInputs,
    const std::vector<App::DocumentObject*>& resources = {}
);

RobotGuiExport void setReplacedInputs(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& replacedInputs
);

RobotGuiExport void recompute(const std::vector<App::Document*>& documents);

RobotGuiExport void commit(Gui::ExactTransaction& transaction);

RobotGuiExport void ensureEditTransaction(App::DocumentObject& object, const char* transactionName);

RobotGuiExport bool resetEdit(const App::DocumentObject& object) noexcept;

/// Return one complete quoted Python string literal.
RobotGuiExport std::string pythonString(const std::string& value);

}  // namespace RobotGui::OperationSupport
