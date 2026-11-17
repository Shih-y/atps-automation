"""Build the Windows desktop application.

Run from anywhere with the interpreter that has requirements.txt and
requirements-desktop.txt installed:

    python build_windows.py           # dist/ATPS-Automation.exe
    python build_windows.py --onedir  # dist/ATPS-Automation/

Keep the .exe.config beside its executable and retain _internal in directory builds.
"""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--onedir', action='store_true',
                        help='build a launch-ready directory without per-run extraction')
    args = parser.parse_args()
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
    dist = HERE / 'dist'
    work = HERE / 'build'
    app_dir = dist / NAME if args.onedir else dist
    # PyInstaller replaces the selected bundle and cleans its work directory.
    # Check the resolved paths before handing them to its cleanup routines.
    for path in (dist, work, work / NAME, app_dir):
        if path.resolve() != path:
            raise SystemExit(f'Build output path is redirected: {path}')

    build_args = [
        '--noconfirm', '--clean', '--onedir' if args.onedir else '--onefile', '--windowed',
        '--name', NAME,
        '--distpath', str(dist),
        '--workpath', str(work),
        '--specpath', str(work),
        '--paths', str(HERE),
        '--collect-submodules', 'serial',
    ]
    for module in HIDDEN:
        build_args += ['--hidden-import', module]
    for item in DATAS:
        source = HERE / item
        target = '.' if item.endswith('.json') else item
        build_args += ['--add-data', f'{source}{os.pathsep}{target}']

    from PyInstaller.__main__ import run
    run(build_args + [str(HERE / 'desktop.py')])

    exe = app_dir / f'{NAME}.exe'
    if not exe.is_file() or exe.stat().st_size < 1_000_000:
        raise SystemExit(f'build produced no usable executable: {exe}')
    runtime_config = copy_runtime_config(exe)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f'\nBuilt {exe} ({exe.stat().st_size / 1048576:.1f} MiB)')
    print(f'SHA256 {digest}')
    print(f'Keep alongside executable: {runtime_config.name}')
    if args.onedir:
        print('Distribute the complete application directory, including _internal.')
    print(f'Config SHA256 {hashlib.sha256(runtime_config.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
