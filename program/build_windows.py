"""Build the one-file Windows desktop executable.

Run from anywhere with the interpreter that has requirements.txt and
requirements-desktop.txt installed:

    python build_windows.py

Output: dist/ATPS-Automation.exe
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = 'ATPS-Automation'
DATAS = ('static', 'vendor', 'config.default.json', 'water_dose_reference.default.json')
HIDDEN = ('webview.platforms.winforms', 'webview.platforms.edgechromium')


def main():
    if sys.platform != 'win32':
        raise SystemExit('This build targets Windows only.')
    if sys.maxsize <= 2 ** 32:
        raise SystemExit('Build with 64-bit Python.')
    for module in ('PyInstaller', 'webview', 'serial'):
        try:
            __import__(module)
        except ImportError as exc:
            raise SystemExit(f'{module} is missing; install requirements-desktop.txt first: {exc}')

    os.chdir(HERE)
    if (HERE / 'build').exists():
        shutil.rmtree(HERE / 'build')
    if (HERE / 'dist').exists():
        shutil.rmtree(HERE / 'dist')

    args = [
        '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', NAME,
        '--distpath', str(HERE / 'dist'),
        '--workpath', str(HERE / 'build'),
        '--specpath', str(HERE / 'build'),
        '--paths', str(HERE),
        '--collect-submodules', 'serial',
    ]
    for module in HIDDEN:
        args += ['--hidden-import', module]
    for item in DATAS:
        source = HERE / item
        target = '.' if item.endswith('.json') else item
        args += ['--add-data', f'{source}{os.pathsep}{target}']

    from PyInstaller.__main__ import run
    run(args + [str(HERE / 'desktop.py')])

    exe = HERE / 'dist' / f'{NAME}.exe'
    if not exe.is_file() or exe.stat().st_size < 1_000_000:
        raise SystemExit(f'build produced no usable executable: {exe}')
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f'\nBuilt {exe} ({exe.stat().st_size / 1048576:.1f} MiB)')
    print(f'SHA256 {digest}')


if __name__ == '__main__':
    main()
