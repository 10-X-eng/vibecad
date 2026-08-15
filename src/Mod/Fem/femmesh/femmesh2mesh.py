# ***************************************************************************
# *   Copyright (c) 2023 Peter McB                                          *
# *   added the function, mesh_2_femmesh, to convert the MESH               *
# *                     into a triangular FEMMESH                           *
# *                                                                         *
# *   Copyright (c) 2016 Frantisek Loeffelmann <LoffF@email.cz>             *
# *   extension to the work of:                                             *
#         Frantisek Loeffelmann, Ulrich Brammer, Bernd Hahnebach            *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

__title__ = "FemMesh to Mesh converter"
__author__ = "Frantisek Loeffelmann, Ulrich Brammer, Bernd Hahnebach, Peter McB"
__url__ = "https://www.freecad.org"

## @package FwmMesh2Mesh
#  \ingroup FEM

import time
import math

import FreeCAD
import Fem

# import Mesh


"""
from os.path import join
the_file = join(FreeCAD.getResourceDir(), "examples", "FEMExample.FCStd")
fc_file = FreeCAD.openDocument(the_file)
fem_mesh = fc_file.getObject("FEMMeshGmsh").FemMesh
result = fc_file.getObject("CCX_Results")
scale = 1  # displacement scale factor
from femmesh import femmesh2mesh
out_mesh = femmesh2mesh.femmesh_2_mesh(fem_mesh, result, scale)
import Mesh
Mesh.show(Mesh.Mesh(out_mesh))

"""
# These dictionaries list the nodes, that define faces of an element.
# The key is the face number, used internally by FreeCAD.
# The list contains the nodes in the element for each face.
tetFaces = {1: [0, 1, 2], 2: [0, 3, 1], 3: [1, 3, 2], 4: [2, 3, 0]}

pentaFaces = {1: [0, 1, 2], 2: [3, 5, 4], 3: [0, 3, 4, 1], 4: [1, 4, 5, 2], 5: [0, 2, 5, 3]}

hexaFaces = {  # hexa8 or hexa20 (ignoring mid-nodes)
    1: [0, 1, 2, 3],
    2: [4, 7, 6, 5],
    3: [0, 4, 5, 1],
    4: [1, 5, 6, 2],
    5: [2, 6, 7, 3],
    6: [3, 7, 4, 0],
}

pyraFaces = {  # pyra5 or pyra13 (ignoring mid-nodes)
    1: [0, 1, 2, 3],
    2: [0, 4, 1],
    3: [1, 4, 2],
    4: [2, 4, 3],
    5: [3, 4, 0],
}

face_dicts = {
    4: tetFaces,
    5: pyraFaces,
    6: pentaFaces,
    8: hexaFaces,
    10: tetFaces,
    13: pyraFaces,
    15: pentaFaces,
    20: hexaFaces,
}


def femmesh_exterior_faces(myFemMesh):
    """Return deterministic exterior faces as tuples of corner-node IDs."""
    if myFemMesh is None:
        raise ValueError("A FEM mesh is required")
    exterior = []
    if int(myFemMesh.VolumeCount) > 0:
        occurrences = {}
        oriented_faces = {}
        for element_id in myFemMesh.Volumes:
            element_nodes = tuple(int(value) for value in myFemMesh.getElementNodes(element_id))
            face_definition = face_dicts.get(len(element_nodes))
            if face_definition is None:
                raise ValueError(
                    f"Volume element {element_id} has unsupported node count {len(element_nodes)}"
                )
            for node_indices in face_definition.values():
                face_nodes = tuple(element_nodes[index] for index in node_indices)
                identity = tuple(sorted(face_nodes))
                occurrences[identity] = occurrences.get(identity, 0) + 1
                oriented_faces.setdefault(identity, face_nodes)
        exterior = [
            oriented_faces[identity]
            for identity in sorted(occurrences)
            if occurrences[identity] == 1
        ]
    elif int(myFemMesh.FaceCount) > 0:
        for element_id in sorted(int(value) for value in myFemMesh.Faces):
            element_nodes = tuple(int(value) for value in myFemMesh.getElementNodes(element_id))
            if len(element_nodes) in (3, 6):
                exterior.append(element_nodes[:3])
            elif len(element_nodes) in (4, 8):
                exterior.append(element_nodes[:4])
            else:
                raise ValueError(
                    f"Face element {element_id} has unsupported node count {len(element_nodes)}"
                )
    else:
        raise ValueError("The FEM mesh has no volume or face surface to convert")
    if not exterior:
        raise ValueError("The FEM mesh has no exterior faces")
    return tuple(exterior)


def femmesh_surface_triangles(myFemMesh, myResults=None, myDispScale=1):
    """Return deterministic exterior-surface triangles for one FEM mesh.

    Unlike the legacy converter below, face identity uses an unbounded tuple
    of node IDs instead of packing IDs into fixed-width integer fields.  This
    keeps meshes with sparse or large node IDs correct.  The function is
    additive so established callers of :func:`femmesh_2_mesh` retain their
    existing behavior.
    """

    exterior = femmesh_exterior_faces(myFemMesh)
    try:
        displacement_scale = float(myDispScale)
    except (TypeError, ValueError) as exc:
        raise TypeError("The displacement scale must be one finite number") from exc
    if not math.isfinite(displacement_scale):
        raise ValueError("The displacement scale must be finite")

    displacements = None
    if myResults is not None:
        node_numbers = tuple(int(value) for value in myResults.NodeNumbers)
        vectors = tuple(myResults.DisplacementVectors)
        if len(node_numbers) != len(vectors) or len(node_numbers) != len(set(node_numbers)):
            raise ValueError("The FEM result has inconsistent displacement node data")
        displacements = dict(zip(node_numbers, vectors))

    def point(node_id):
        try:
            value = myFemMesh.getNodeById(node_id)
        except Exception as exc:
            raise ValueError(f"FEM surface node {node_id} is unavailable") from exc
        if not all(math.isfinite(float(getattr(value, axis))) for axis in ("x", "y", "z")):
            raise ValueError(f"FEM surface node {node_id} has non-finite coordinates")
        if displacements is not None:
            if node_id not in displacements:
                raise ValueError(f"FEM result has no displacement for surface node {node_id}")
            displacement = displacements[node_id]
            if not all(
                math.isfinite(float(getattr(displacement, axis)))
                for axis in ("x", "y", "z")
            ):
                raise ValueError(
                    f"FEM result displacement for surface node {node_id} is non-finite"
                )
            value = value + displacement * displacement_scale
            if not all(
                math.isfinite(float(getattr(value, axis))) for axis in ("x", "y", "z")
            ):
                raise ValueError(
                    f"Deformed FEM surface node {node_id} has non-finite coordinates"
                )
        return value

    triangles = []
    for face_nodes in exterior:
        triangles.extend((point(face_nodes[0]), point(face_nodes[1]), point(face_nodes[2])))
        if len(face_nodes) == 4:
            triangles.extend((point(face_nodes[2]), point(face_nodes[3]), point(face_nodes[0])))
    if not triangles:
        raise ValueError("The FEM mesh has no exterior triangles")
    return triangles


def femmesh_2_mesh(myFemMesh, myResults=None, myDispScale=1):
    shiftBits = 20  # allows a million nodes, needs to be higher for more nodes in a FemMesh

    # This code generates a dict and a faceCode for each face of all elements
    # All faceCodes are than sorted.

    start_time = time.process_time()
    faceCodeList = []
    faceCodeDict = {}

    if myFemMesh.VolumeCount > 0:
        for ele in myFemMesh.Volumes:
            element_nodes = myFemMesh.getElementNodes(ele)
            # print("element_node: ", element_nodes)
            faceDef = face_dicts[len(element_nodes)]

            for key in faceDef:
                nodeList = []
                codeList = []
                faceCode = 0
                shifter = 0
                for nodeIdx in faceDef[key]:
                    nodeList.append(element_nodes[nodeIdx])
                    codeList.append(element_nodes[nodeIdx])
                codeList.sort()
                for node in codeList:
                    faceCode += node << shifter
                    # x << n: x shifted left by n bits = Multiplication
                    shifter += shiftBits
                # print("codeList: ", codeList)
                faceCodeDict[faceCode] = nodeList
                faceCodeList.append(faceCode)
    elif myFemMesh.FaceCount > 0:
        for ele in myFemMesh.Faces:
            element_nodes = myFemMesh.getElementNodes(ele)
            # print("element_node: ", element_nodes)
            if len(element_nodes) in [3, 6]:
                faceDef = {1: [0, 1, 2]}
            else:  # quad element
                faceDef = {1: [0, 1, 2, 3]}

            for key in faceDef:
                nodeList = []
                codeList = []
                faceCode = 0
                shifter = 0
                for nodeIdx in faceDef[key]:
                    nodeList.append(element_nodes[nodeIdx])
                    codeList.append(element_nodes[nodeIdx])
                codeList.sort()
                for node in codeList:
                    faceCode += node << shifter
                    # x << n: x shifted left by n bits = Multiplication
                    shifter += shiftBits
                # print("codeList: ", codeList)
                faceCodeDict[faceCode] = nodeList
                faceCodeList.append(faceCode)

    faceCodeList.sort()
    allFaces = len(faceCodeList)
    actFaceIdx = 0
    singleFaces = []
    # Here we search for faces, which do not have a counterpart.
    # These are the faces on the surface of the mesh.
    while actFaceIdx < allFaces:
        if actFaceIdx < (allFaces - 1):
            if faceCodeList[actFaceIdx] == faceCodeList[actFaceIdx + 1]:
                actFaceIdx += 2
            else:
                # print("found a single Face: ", faceCodeList[actFaceIdx])
                singleFaces.append(faceCodeList[actFaceIdx])
                actFaceIdx += 1
        else:
            FreeCAD.Console.PrintMessage(f"Found a last Face: {faceCodeList[actFaceIdx]}\n")
            singleFaces.append(faceCodeList[actFaceIdx])
            actFaceIdx += 1

    output_mesh = []
    if myResults:
        FreeCAD.Console.PrintMessage(f"{myResults.Name}\n")
        for myFace in singleFaces:
            face_nodes = faceCodeDict[myFace]
            dispVec0 = myResults.DisplacementVectors[myResults.NodeNumbers.index(face_nodes[0])]
            dispVec1 = myResults.DisplacementVectors[myResults.NodeNumbers.index(face_nodes[1])]
            dispVec2 = myResults.DisplacementVectors[myResults.NodeNumbers.index(face_nodes[2])]
            triangle = [
                myFemMesh.getNodeById(face_nodes[0]) + dispVec0 * myDispScale,
                myFemMesh.getNodeById(face_nodes[1]) + dispVec1 * myDispScale,
                myFemMesh.getNodeById(face_nodes[2]) + dispVec2 * myDispScale,
            ]
            output_mesh.extend(triangle)
            # print("my triangle: ", triangle)
            if len(face_nodes) == 4:
                dispVec3 = myResults.DisplacementVectors[myResults.NodeNumbers.index(face_nodes[3])]
                triangle = [
                    myFemMesh.getNodeById(face_nodes[2]) + dispVec2 * myDispScale,
                    myFemMesh.getNodeById(face_nodes[3]) + dispVec3 * myDispScale,
                    myFemMesh.getNodeById(face_nodes[0]) + dispVec0 * myDispScale,
                ]
                output_mesh.extend(triangle)
                # print("my 2. triangle: ", triangle)

    else:
        for myFace in singleFaces:
            face_nodes = faceCodeDict[myFace]
            triangle = [
                myFemMesh.getNodeById(face_nodes[0]),
                myFemMesh.getNodeById(face_nodes[1]),
                myFemMesh.getNodeById(face_nodes[2]),
            ]
            output_mesh.extend(triangle)
            # print("my triangle: ", triangle)
            if len(face_nodes) == 4:
                triangle = [
                    myFemMesh.getNodeById(face_nodes[2]),
                    myFemMesh.getNodeById(face_nodes[3]),
                    myFemMesh.getNodeById(face_nodes[0]),
                ]
                output_mesh.extend(triangle)
                # print("my 2. triangle: ", triangle)

    end_time = time.process_time()
    FreeCAD.Console.PrintMessage(f"Mesh by surface search method: {end_time - start_time}\n")
    return output_mesh


# additional function to convert mesh to femmesh
def mesh_2_femmesh(myFemMesh, singleFaces, faceCodeDict):
    start_time = time.process_time()
    femmesh = Fem.FemMesh()
    myfemmesh = myFemMesh.Nodes
    # nodes contains the nodes that are used
    nodes = {}
    for myFace in singleFaces:
        face_nodes = faceCodeDict[myFace]
        for j in (0, 1, 2):
            try:
                nodes[face_nodes[j]] += 1
            except:
                nodes[face_nodes[j]] = 0
        if len(face_nodes) == 4:
            j = 3
            try:
                nodes[face_nodes[j]] += 1
            except:
                nodes[face_nodes[j]] = 0
    sfNode = femmesh.addNode
    sfFace = femmesh.addFace
    for key in myFemMesh.Nodes:
        mynode = myfemmesh[key]
        try:
            if nodes[key] >= 0:
                sfNode(mynode[0], mynode[1], mynode[2], key)
        except:
            pass

    output_mesh = []

    for myFace in singleFaces:
        face_nodes = faceCodeDict[myFace]
        sfFace(face_nodes[0], face_nodes[1], face_nodes[2])
        if len(face_nodes) == 4:
            sfFace(face_nodes[2], face_nodes[3], face_nodes[0])
    obj = FreeCAD.ActiveDocument.addObject("Fem::FemMeshObject", "Mesh2Fem")
    obj.FemMesh = femmesh
    end_time = time.process_time()
    FreeCAD.Console.PrintMessage(f"Convert to FemMesh: {end_time - start_time}\n")
    return obj


# end of mesh_2_femmesh
