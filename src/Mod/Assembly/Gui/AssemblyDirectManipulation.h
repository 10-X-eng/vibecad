// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>

#include <QObject>

#include <Inventor/SbVec2s.h>

#include <fastsignals/connection.h>

namespace Gui
{
class MDIView;
class View3DInventorViewer;
}  // namespace Gui

namespace AssemblyGui
{

class ViewProviderAssembly;

class AssemblyDirectManipulation final: public QObject
{
public:
    static void install();

private:
    explicit AssemblyDirectManipulation(QObject* parent);
    ~AssemblyDirectManipulation() override;

    bool eventFilter(QObject* watched, QEvent* event) override;

    void refreshViewer();
    void setViewer(Gui::View3DInventorViewer* viewer);
    bool supportsActiveWorkbench() const;

    void beginCandidate(const SbVec2s& position);
    bool moveCandidate(const SbVec2s& position);
    bool finishCandidate(bool commit);
    void clearCandidate();

    ViewProviderAssembly* resolveCandidate() const;
    ViewProviderAssembly* resolveAssemblyAtPreselection() const;

    Gui::View3DInventorViewer* viewer = nullptr;
    std::string documentUid;
    long assemblyId {-1};
    SbVec2s pressPosition;
    bool leftButtonDown {false};
    bool moving {false};

    fastsignals::scoped_connection activateWorkbenchConnection;
    fastsignals::scoped_connection activateViewConnection;
    fastsignals::scoped_connection closeViewConnection;
    fastsignals::scoped_connection enterEditConnection;
};

}  // namespace AssemblyGui
