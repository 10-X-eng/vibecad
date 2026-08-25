# SPDX-License-Identifier: LGPL-2.1-or-later

from femviewprovider import view_base_femobject


class VPSolverOpenFOAM(view_base_femobject.VPBaseFemObject):
    def supportsDocumentTimelineEdit(self):
        return True

    def __init__(self, vobj):
        super().__init__(vobj)
        vobj.addExtension("Gui::ViewProviderSuppressibleExtensionPython")

    def getIcon(self):
        return ":/icons/FEM_SolverStandard.svg"
