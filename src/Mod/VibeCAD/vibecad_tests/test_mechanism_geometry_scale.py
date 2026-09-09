# SPDX-License-Identifier: LGPL-2.1-or-later

from itertools import combinations
import random

import pytest

import VibeCADMechanismGeometry as geometry


@pytest.mark.parametrize('count', [337, 10_000])
def test_collision_summary_accepts_large_assemblies(count):
    names = [f'Component_{index}' for index in range(count)]
    frames = [dict(frame_index=index + 1, nominal_time_s=index / 10,
                   collisions=[]) for index in range(22)]
    frames[-1]['collisions'] = [dict(first_component=names[0],
        second_component=names[-1], interference_volume_mm3=1.0,
        common_solid_count=1)]
    summary = geometry.summarize_dynamic_collision_frames(names, frames)
    assert summary['component_count'] == count
    assert summary['possible_pair_count'] == count * (count - 1) // 2
    assert summary['evaluated_frame_count'] == 22
    assert summary['colliding_pair_count'] == 1
    assert summary['first_collision']['frame_index'] == 22


@pytest.mark.parametrize('names', [[], 'Body', ['Body', 'Body'], ['bad name']])
def test_collision_summary_preserves_identifier_validation(names):
    with pytest.raises(geometry.MechanismGeometryError):
        geometry.summarize_dynamic_collision_frames(names,
            [dict(frame_index=1, nominal_time_s=0, collisions=[])])


def test_explicit_pairs_are_limited_by_identifiers_not_fixed_count():
    names = [f'Component_{index}' for index in range(337)]
    pairs = list(combinations(names, 2))
    assert geometry._component_pairs(pairs, component_names=set(names)) == pairs
    for bad_pair in [(names[0], names[0]), (names[0], 'Unknown'), pairs[0]]:
        with pytest.raises(geometry.MechanismGeometryError):
            geometry._component_pairs(pairs + [bad_pair], component_names=set(names))


def test_static_preparation_accepts_large_assemblies(monkeypatch):
    components = {f'Component_{index}': object() for index in range(10_000)}
    monkeypatch.setattr(geometry, '_placed_shape', lambda value, **kwargs: value)
    monkeypatch.setattr(geometry, '_bounds', lambda shape, **kwargs: {})
    placed, bounds = geometry._prepared_components(components)
    assert placed == components
    assert bounds.keys() == components.keys()


def test_spatial_candidates_match_exhaustive_aabb_including_touching():
    rng = random.Random(337)
    bounds = {}
    for index in range(337):
        lo = [rng.randint(-10, 10) for _ in range(3)]
        bounds[f'Component_{index}'] = dict(minimum_mm=lo,
            maximum_mm=[value + rng.randint(0, 4) for value in lo])
    expected = {pair for pair in combinations(bounds, 2)
                if geometry._aabb_evidence(*(bounds[name] for name in pair))[
                    'overlaps_or_touches']}
    actual = list(geometry._overlapping_component_pairs(bounds))
    assert set(actual) == expected
    assert len(actual) == len(expected)


@pytest.mark.parametrize('axis', [0, 1, 2])
def test_sparse_ten_thousand_components_do_not_scan_all_pairs(axis, monkeypatch):
    bounds = {}
    for index in range(10_000):
        lo = [0.0, 0.0, 0.0]
        lo[axis] = index * 2.0
        bounds[f'Component_{index}'] = dict(minimum_mm=lo,
            maximum_mm=[value + 1 for value in lo])
    calls = 0
    original = geometry._aabb_evidence

    def checked(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(geometry, '_aabb_evidence', checked)
    assert list(geometry._overlapping_component_pairs(bounds)) == []
    assert calls < len(bounds)
