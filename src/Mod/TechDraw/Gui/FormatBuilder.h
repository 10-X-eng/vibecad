/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public License   *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                    *
 ***************************************************************************/

#pragma once

#include <string>

#include <Mod/TechDraw/TechDrawGlobal.h>


namespace App
{
class DocumentObject;
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingFormatCustomization
{
    std::string targetKind;
    std::string value;
    std::string preview;
};

TechDrawGuiExport DrawingFormatCustomization validateDrawingFormatCustomization(
    App::DocumentObject* object,
    const std::string& value);

TechDrawGuiExport DrawingFormatCustomization applyDrawingFormatCustomization(
    App::DocumentObject* object,
    const std::string& value);

}  // namespace TechDrawGui
