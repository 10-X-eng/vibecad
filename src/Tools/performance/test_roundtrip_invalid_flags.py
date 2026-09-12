"""Test the probe's pure flag comparison without importing the GUI runtime."""
import ast
from pathlib import Path

import pytest


def check(expected, actual, allowed=()):
    tree = ast.parse(Path(__file__).with_name('portable_roundtrip_probe.py').read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'check_invalid_flags')
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<probe>', 'exec'), namespace)
    return namespace['check_invalid_flags'](expected, actual, allowed)


def test_saved_invalid_flags_are_strict_by_default():
    assert check(['assembly'], ['assembly']) == []
    with pytest.raises(RuntimeError):
        check(['assembly'], [])


def test_only_explicitly_named_cleared_flags_are_admitted():
    assert check(['assembly'], [], ['assembly']) == ['assembly']
    with pytest.raises(RuntimeError):
        check(['assembly', 'body'], [], ['assembly'])
    with pytest.raises(RuntimeError):
        check(['assembly'], ['new_failure'], ['assembly'])
