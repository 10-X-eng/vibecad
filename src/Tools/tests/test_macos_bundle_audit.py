# SPDX-License-Identifier: LGPL-2.1-or-later

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[3] / 'package/rattler-build/scripts'
with patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
    spec = importlib.util.spec_from_file_location('audit_macos_bundle', SCRIPTS / 'audit_macos_bundle.py')
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)


class TestMacOSBundleAudit(unittest.TestCase):
    def validate(self, binary, dependency, command='LC_LOAD_WEAK_DYLIB'):
        bundle = Path('/test/VibeCAD.app')
        audit._validate_path(
            dependency, command=command, file_path=bundle / binary,
            bundle=bundle, forbidden_prefixes=(),
        )

    def test_optional_driver_weak_link_propagates_to_gui_consumers(self):
        driver = '/Library/Frameworks/3DconnexionClient.framework/Versions/A/3DconnexionClient'
        for binary in ('Contents/Resources/bin/freecad',
                       'Contents/Resources/lib/AssemblyGui.so',
                       'Contents/Resources/lib/FreeCADGui.so',
                       'Contents/Resources/lib/libFreeCADGui.dylib'):
            with self.subTest(binary=binary):
                self.validate(binary, driver)

    def test_required_driver_and_unknown_external_links_remain_rejected(self):
        driver = '/Library/Frameworks/3DconnexionClient.framework/Versions/A/3DconnexionClient'
        for dependency, command in (
            (driver, 'LC_LOAD_DYLIB'),
            (driver, 'LC_REEXPORT_DYLIB'),
            (driver, 'LC_RPATH'),
            (driver.replace('Versions/A', 'Versions/B'), 'LC_LOAD_WEAK_DYLIB'),
            ('/Library/Frameworks/Unknown.framework/Unknown', 'LC_LOAD_WEAK_DYLIB'),
            ('/opt/homebrew/lib/libexample.dylib', 'LC_LOAD_WEAK_DYLIB'),
        ):
            with self.subTest(dependency=dependency, command=command):
                with self.assertRaises(RuntimeError):
                    self.validate('Contents/Resources/bin/freecad', dependency, command)
