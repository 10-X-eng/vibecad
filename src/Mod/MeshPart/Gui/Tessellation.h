// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <QPointer>
#include <memory>
#include <string>

#include <Gui/TaskView/TaskDialog.h>
#include <Gui/TaskView/TaskView.h>
#include <Mod/Mesh/Gui/RemeshGmsh.h>


template<typename T>
class QFutureWatcher;
class QProgressDialog;


namespace App
{
class Document;
class SubObjectT;
}  // namespace App
namespace MeshPartGui
{

/**
 * Non-modal dialog to mesh a shape.
 * @author Werner Mayer
 */
class Mesh2ShapeGmsh: public MeshGui::GmshWidget
{
public:
    explicit Mesh2ShapeGmsh(QWidget* parent = nullptr, Qt::WindowFlags fl = Qt::WindowFlags());
    ~Mesh2ShapeGmsh() override;

    [[nodiscard]] int algorithm() const;
    [[nodiscard]] double minimumSize() const;
    [[nodiscard]] double maximumSize() const;
    [[nodiscard]] std::string executable() const;
};

class Ui_Tessellation;
class Tessellation: public QWidget
{
    Q_OBJECT

    enum
    {
        Standard,
        Mefisto,
        Netgen,
        Gmsh
    };

    enum
    {
        VeryCoarse = 0,
        Coarse = 1,
        Moderate = 2,
        Fine = 3,
        VeryFine = 4
    };

public:
    explicit Tessellation(QWidget* parent = nullptr);
    ~Tessellation() override;
    bool accept();
    void reject();

protected:
    void changeEvent(QEvent* e) override;
    void saveParameters(int method);

private:
    bool processAndCommit(
        int method,
        App::Document* doc,
        const std::list<App::SubObjectT>&
    );
    void setupConnections();
    void onEstimateMaximumEdgeLengthClicked();
    void onComboFinenessCurrentIndexChanged(int);
    void onCheckSecondOrderToggled(bool);
    void onCheckQuadDominatedToggled(bool);

private:
    class SelectionState;
    QString document;
    QPointer<Mesh2ShapeGmsh> gmsh;
    QFutureWatcher<double>* edgeEstimateWatcher;
    QPointer<QProgressDialog> edgeEstimateProgress;
    std::unique_ptr<SelectionState> selectionState;
    std::unique_ptr<Ui_Tessellation> ui;
};

class TaskTessellation: public Gui::TaskView::TaskDialog
{
    Q_OBJECT

public:
    TaskTessellation();

public:
    void open() override;
    void clicked(int) override;
    bool accept() override;
    bool reject() override;

    QDialogButtonBox::StandardButtons getStandardButtons() const override
    {
        return QDialogButtonBox::Ok | QDialogButtonBox::Cancel;
    }

private:
    Tessellation* widget;
};

}  // namespace MeshPartGui
