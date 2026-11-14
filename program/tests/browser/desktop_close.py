"""Send normal WM_CLOSE only to the native window of our logged test process."""
import ctypes
import re
import sys
from pathlib import Path
from ctypes import wintypes

directory = Path(sys.argv[1]).resolve()
if 'test_output' not in directory.parts:
    raise SystemExit('Only test_output instances may be closed')
pid = int(re.findall(r'desktop start pid=(\d+)', (directory / 'desktop.log').read_text(encoding='utf8'))[-1])
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
matched = []

@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def visit(handle, unused):
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
    if owner.value == pid:
        title = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(handle, title, len(title))
        if title.value == '双水相配液与图像记录平台':
            if not user32.PostMessageW(handle, 0x0010, 0, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            matched.append(int(handle))
    return True

user32.EnumWindows(visit, 0)
if not matched:
    raise SystemExit('Owned ATPS test window was not found')
