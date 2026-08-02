// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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


#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Mod/Robot/Gui/TaskDlgEdge2Trac.h>

#include "OperationSupport.h"
#include "ViewProviderEdge2TracObject.h"


using namespace Gui;
using namespace RobotGui;

PROPERTY_SOURCE(RobotGui::ViewProviderEdge2TracObject, RobotGui::ViewProviderTrajectory)

bool ViewProviderEdge2TracObject::doubleClicked()
{
    auto* document = getDocument();
    return document && document->setEdit(this, Gui::ViewProvider::Default);
}


bool ViewProviderEdge2TracObject::setEdit(int)
{
    auto* object = getObject<Robot::Edge2TracObject>();
    if (!object || !RobotGui::OperationSupport::isUsableObject(object)) {
        return false;
    }
    Gui::Control().showDialog(new TaskDlgEdge2Trac(object), object->getDocument());
    return true;
}

void ViewProviderEdge2TracObject::unsetEdit(int)
{}
