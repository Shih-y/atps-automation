"""App-local CLR config; subprocess probes do not create windows or devices."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from build_windows import copy_runtime_config


class DesktopRuntimeConfigTests(unittest.TestCase):
    def test_sidecar_tracks_exact_executable_name_and_changes_no_executable_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'ATPS-Automation-vTest.exe'
            executable.write_bytes(b'executable fixture')
            path = copy_runtime_config(executable)
            self.assertEqual('ATPS-Automation-vTest.exe.config', path.name)
            self.assertEqual(b'executable fixture', executable.read_bytes())
            tree = ET.parse(path)
            self.assertEqual('true', tree.find('./runtime/loadFromRemoteSources').get('enabled'))
            self.assertEqual(['runtime'], [node.tag for node in tree.getroot()])

    @unittest.skipUnless(sys.platform == 'win32', '.NET Framework probe requires Windows')
    def test_configured_subprocess_can_load_pythonnet_from_project_path(self):
        base = Path(sys.base_prefix)
        dll_name = 'python%d%d.dll' % sys.version_info[:2]
        if not all((base / name).is_file() for name in ('python.exe', dll_name)):
            self.skipTest('Base interpreter executable/DLL not available for an isolated probe')
        spec = importlib.util.find_spec('pythonnet')
        packages = Path(spec.origin).parent.parent if spec and spec.origin else None
        candidate = Path(__file__).resolve().parents[2] / '.venv' / 'Lib' / 'site-packages'
        if packages is None and (candidate / 'pythonnet' / '__init__.py').is_file():
            packages = candidate
        if packages is None:
            self.skipTest('Python.NET is optional outside desktop build environments')
        output = Path(__file__).resolve().parents[1] / 'test_output'
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output, prefix='clr-local-') as folder:
            probe = Path(folder)
            for name in ('python.exe', dll_name, 'python3.dll'):
                if (base / name).is_file():
                    shutil.copyfile(base / name, probe / name)
            copy_runtime_config(probe / 'python.exe')
            env = dict(os.environ, PYTHONHOME=str(base), PYTHONPATH=str(packages))
            for key in list(env):
                if key.lower() == 'complus_loadfromremotesources':
                    del env[key]
            result = subprocess.run(
                [str(probe / 'python.exe'), '-c',
                 'import clr;import System;print("ATPS_CLR_OK",System.Environment.Version)'],
                env=env, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('ATPS_CLR_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
