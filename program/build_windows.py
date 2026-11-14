"""Build the one-file Windows desktop executable.

Run from anywhere with the interpreter that has requirements.txt and
requirements-desktop.txt installed:

    python build_windows.py

Output: dist/ATPS-Automation.exe and its application-local .exe.config.
Distribute both files together; the configuration does not change Windows policy.
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = 'ATPS-Automation'
DATAS = ('static', 'vendor', 'config.default.json', 'water_dose_reference.default.json','build_info.json')
HIDDEN = ('webview.platforms.winforms', 'webview.platforms.edgechromium')


def copy_runtime_config(executable):
    """The CLR reads this exact executable-name suffix before Python.NET loads."""
    executable = Path(executable)
    destination = executable.with_name(executable.name + '.config')
    shutil.copyfile(HERE / 'desktop.netfx.config', destination)
    return destination


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
    from software_version import version_info
    info=version_info()
    (HERE/'build_info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    if (HERE / 'build').exists():
        if (HERE/'build').resolve().parent!=HERE:raise SystemExit('Build path escapes workspace')
        shutil.rmtree(HERE / 'build')
    if (HERE / 'dist').exists():
        if (HERE/'dist').resolve().parent!=HERE:raise SystemExit('Dist path escapes workspace')
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
    runtime_config = copy_runtime_config(exe)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f'\nBuilt {exe} ({exe.stat().st_size / 1048576:.1f} MiB)')
    print(f'SHA256 {digest}')
    print(f'Keep alongside executable: {runtime_config.name}')
    print(f'Config SHA256 {hashlib.sha256(runtime_config.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
