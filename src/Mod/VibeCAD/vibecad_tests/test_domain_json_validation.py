# SPDX-License-Identifier: LGPL-2.1-or-later
"""Strict worker-data validation must inspect values, not format disposable JSON."""

import json

import pytest

import VibeCADVibeScriptDomainRuntime as runtime


def test_validation_does_not_encode_discarded_text(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Validation formatted JSON that no caller consumes')

    monkeypatch.setattr(runtime.json.JSONEncoder, 'iterencode', forbidden)
    runtime._validate_json_serializable({
        'frames': [{'matrix': [float(i) for i in range(16)]} for _ in range(1000)],
        'label': '\ud800',
    })


@pytest.mark.parametrize('value', [
    None, True, False, 42, -42, 10**5000, 1.25, float('nan'), float('inf'),
    float('-inf'), 'text', '\ud800', [1, 2], (1, 2), {1, 2}, b'text', object(),
    {'a': [None, {'b': 2}]}, {1: 'a', 2: 'b'}, {True: 'a'}, {None: 'a'},
    {1.25: 'a'}, {float('nan'): 'a'}, {(1,): 'a'}, {'a': 1, 2: 'b'},
], ids=lambda value: type(value).__name__)
def test_validation_matches_strict_encoder(value):
    encoder = json.JSONEncoder(ensure_ascii=True, sort_keys=True, allow_nan=False)
    try:
        for _chunk in encoder.iterencode(value):
            pass
    except (TypeError, ValueError) as error:
        with pytest.raises(type(error)):
            runtime._validate_json_serializable(value)
    else:
        runtime._validate_json_serializable(value)


def test_cycles_rejected_but_shared_values_allowed():
    child = {'values': [1, 2, 3]}
    runtime._validate_json_serializable([child, child])
    child['cycle'] = child
    with pytest.raises(ValueError, match='Circular reference'):
        runtime._validate_json_serializable(child)
