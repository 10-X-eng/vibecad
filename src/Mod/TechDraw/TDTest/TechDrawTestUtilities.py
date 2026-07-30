import FreeCAD
import os


def createPageWithSVGTemplate(doc=None):
    """Returns a page with an SVGTemplate added on the ActiveDocument"""
    path = os.path.dirname(os.path.abspath(__file__))
    templateFileSpec = path + "/TestTemplate.svg"

    if not doc:
        doc = FreeCAD.ActiveDocument

    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = templateFileSpec
    page = doc.addObject("TechDraw::DrawPage", "Page")
    page.Template = template
    return page
