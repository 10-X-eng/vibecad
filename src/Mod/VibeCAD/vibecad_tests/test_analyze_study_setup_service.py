# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

from VibeCADAnalyzeStudySetup import (
    analyses_in_document,
    analysis_for_selection,
    readiness_rows,
)


class _Object:
    def __init__(self, document, name, type_id, group=()):
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.Group = list(group)

    def isDerivedFrom(self, type_id):
        return self.TypeId == type_id


def test_analysis_choice_uses_exact_selection_membership() -> None:
    document = SimpleNamespace(Objects=[])
    body = _Object(document, "Body", "PartDesign::Body")
    material = _Object(document, "Material", "App::MaterialObjectPython")
    first = _Object(document, "Analysis", "Fem::FemAnalysis", (material,))
    second = _Object(document, "Analysis001", "Fem::FemAnalysis")
    document.Objects = [body, first, material, second]

    assert analyses_in_document(document) == (first, second)
    assert analysis_for_selection(document, (first,)) is first
    assert analysis_for_selection(document, (material,)) is first
    assert analysis_for_selection(document, (body,)) is None


def test_ambiguous_membership_is_not_guessed() -> None:
    document = SimpleNamespace(Objects=[])
    material = _Object(document, "Material", "App::MaterialObjectPython")
    first = _Object(document, "Analysis", "Fem::FemAnalysis", (material,))
    second = _Object(document, "Analysis001", "Fem::FemAnalysis", (material,))
    document.Objects = [first, second, material]

    assert analysis_for_selection(document, (material,)) is None


def test_readiness_rows_report_exact_inventory_without_inventing_progress() -> None:
    rows = readiness_rows(
        {
            "geometry_source_count": 2,
            "material_count": 1,
            "support_count": 1,
            "connection_count": 0,
            "load_count": 1,
            "thermal_condition_count": 0,
            "fluid_constraint_count": 0,
            "electromagnetic_constraint_count": 0,
            "mesh_definition_count": 1,
            "generated_mesh_count": 0,
            "solver_kinds": ["elmer"],
            "result_count": 0,
        }
    )

    assert rows == (
        ("Geometry", "2 sources"),
        ("Materials", "1"),
        ("Conditions", "2"),
        ("Mesh", "defined"),
        ("Solver", "Elmer"),
        ("Results", "0"),
    )
