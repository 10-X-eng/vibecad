# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fusion/SolidWorks-style Sketcher constraint selection contracts.

Symmetric must accept whole lines, not only endpoints. Horizontal, Vertical,
and the other entity constraints that allow both a curve and a vertex must
prefer the curve when the preselected vertex is just that curve's endpoint.
"""

from __future__ import annotations

from pathlib import Path


_REPOSITORY = Path(__file__).resolve().parents[4]
_CONSTRAINTS = (
    _REPOSITORY / "src" / "Mod" / "Sketcher" / "Gui" / "CommandConstraints.cpp"
)
_SCHEMA = (
    _REPOSITORY
    / "src"
    / "Mod"
    / "VibeCAD"
    / "VibeCADNativeSketchConstraintSchema.py"
)


def _source() -> str:
    return _CONSTRAINTS.read_text(encoding="utf-8")


def _command_constructor(source: str, command: str) -> str:
    marker = f'CmdSketcherConstraint("{command}")'
    start = source.index(marker)
    end = source.find("void CmdSketcher", start + len(marker))
    if end < 0:
        end = source.find("\nclass ", start + len(marker))
    return source[start:end]


def _function_section(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unterminated function {signature}")


def test_generic_handler_prefers_a_whole_curve_over_its_own_endpoint() -> None:
    source = _source()
    assert "preferCurveOverEndpoint" in source
    release = _function_section(source, "bool releaseButton(Base::Vector2d onSketchPos) override")
    assert "preferCurveOverEndpoint" in release
    assert "vertexBelongsToCurve" in release
    assert "allowVertex && !preferCurve" in release


def test_entity_constraints_prefer_whole_curves_like_fusion() -> None:
    source = _source()
    entity_commands = (
        "Sketcher_ConstrainSymmetric",
        "Sketcher_ConstrainHorizontal",
        "Sketcher_ConstrainVertical",
        "Sketcher_ConstrainHorVer",
        "Sketcher_ConstrainParallel",
        "Sketcher_ConstrainPerpendicular",
        "Sketcher_ConstrainEqual",
        "Sketcher_ConstrainTangent",
    )
    for command in entity_commands:
        constructor = _command_constructor(source, command)
        assert "preferCurveOverEndpoint = true" in constructor, command


def test_dimensional_constraints_keep_endpoint_selection_behavior() -> None:
    source = _source()
    dimensional_commands = (
        "Sketcher_ConstrainDistance",
        "Sketcher_ConstrainDistanceX",
        "Sketcher_ConstrainDistanceY",
        "Sketcher_ConstrainAngle",
    )
    for command in dimensional_commands:
        constructor = _command_constructor(source, command)
        assert "preferCurveOverEndpoint = true" not in constructor, command


def test_point_constraints_still_prefer_vertices() -> None:
    source = _source()
    coincident = _function_section(
        source,
        "CmdSketcherConstrainCoincidentUnified::CmdSketcherConstrainCoincidentUnified(const char* initName)",
    )
    lock = _command_constructor(source, "Sketcher_ConstrainLock")
    assert "preferCurveOverEndpoint = true" not in coincident
    assert "preferCurveOverEndpoint = true" not in lock


def test_symmetric_accepts_two_whole_lines_and_a_symmetry_line() -> None:
    source = _source()
    constructor = _command_constructor(source, "Sketcher_ConstrainSymmetric")
    activated = _function_section(
        source, "void CmdSketcherConstrainSymmetric::activated(int iMsg)"
    )
    apply = _function_section(
        source, "void CmdSketcherConstrainSymmetric::applyConstraint("
    )
    assert "{SelEdge, SelEdge, SelEdgeOrAxis}" in constructor
    assert "constrainTwoWholeCurvesSymmetric" in activated
    assert "constrainTwoWholeCurvesSymmetric" in apply
    assert "two lines and a symmetry line" in activated


def test_two_whole_curve_symmetry_is_limited_to_straight_lines() -> None:
    source = _source()
    helper = _function_section(source, "bool constrainTwoWholeCurvesSymmetric(")
    assert "isLineSegment(*geom1)" in helper
    assert "isLineSegment(*geom2)" in helper
    assert "isLineSegment(*geom3)" in helper
    assert "isArcOfCircle" not in helper
    assert "isBSplineCurve" not in helper

    schema = _SCHEMA.read_text(encoding="utf-8")
    assert "whole straight lines about" in schema
    assert "two whole open curves" not in schema


def test_interactive_whole_line_symmetry_refuses_all_fixed_geometry() -> None:
    source = _source()
    apply = _function_section(
        source, "void CmdSketcherConstrainSymmetric::applyConstraint("
    )
    whole_line_cases = apply.split(
        "case 15:// {SelEdge, SelEdge, SelEdgeOrAxis}", 1
    )[1].split("default:", 1)[0]
    assert "areAllPointsOrSegmentsFixed" in whole_line_cases
    assert "showNoConstraintBetweenFixedGeometry" in whole_line_cases


def test_symmetric_hints_allow_a_second_line_instead_of_only_a_point() -> None:
    source = _source()
    hints = _function_section(source, "std::list<Gui::InputHint> getToolHints() const override")
    symmetric = hints.split('if (commandName == "Sketcher_ConstrainSymmetric")', 1)[1]
    symmetric = symmetric.split("if (commandName ==", 1)[0]
    first_after_edge = symmetric.split("selectionStep == 1", 1)[1].split(
        "selectionStep == 2", 1
    )[0]
    assert "PICK_SECOND_LINE_OR_SYMMETRY" in first_after_edge
    assert "PICK_SYMMETRY_POINT" not in first_after_edge
    assert "PICK_SYMMETRY_LINE" in symmetric
