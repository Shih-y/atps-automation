"""Exercise real native resize borders of only the logged isolated test window."""
import ctypes
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path

directory = Path(sys.argv[1]).resolve()
if 'test_output' not in directory.parts:
    raise SystemExit('Only test_output instances may be resized')
pid = int(re.findall(r'desktop start pid=(\d+)',
                     (directory / 'desktop.log').read_text(encoding='utf8'))[-1])
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
handles = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def visit(handle, unused):
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
    title = ctypes.create_unicode_buffer(1024)
    if owner.value == pid:
        user32.GetWindowTextW(handle, title, len(title))
        if title.value == '双水相配液与图像记录平台':
            handles.append(handle)
    return True


user32.EnumWindows(visit, 0)
assert len(handles) == 1, 'Owned ATPS test window was not found'
hwnd = handles[0]
style = user32.GetWindowLongPtrW(hwnd, -16)
assert style & 0x00040000, 'Native sizing frame is missing'
assert not style & 0x00C00000, 'System title bar must remain hidden'


def rectangle():
    result = wintypes.RECT()
    assert user32.GetWindowRect(hwnd, ctypes.byref(result))
    return result.left, result.top, result.right, result.bottom


def place(bounds):
    left, top, right, bottom = bounds
    assert user32.SetWindowPos(hwnd, None, left, top, right-left, bottom-top, 0x0014)
    time.sleep(.1)


original = rectangle()
client_origin = wintypes.POINT(0, 0)
assert user32.ClientToScreen(hwnd, ctypes.byref(client_origin))
assert client_origin.y == original[1], ('blank top frame remains', client_origin.y-original[1])
if '--capture' in sys.argv:
    import clr
    clr.AddReference('System.Drawing')
    from System.Drawing import Bitmap, Graphics, Rectangle
    from System.Drawing.Imaging import ImageFormat
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    bitmap = Bitmap(original[2]-original[0], original[3]-original[1])
    graphics = Graphics.FromImage(bitmap)
    dc = graphics.GetHdc()
    try:
        assert user32.PrintWindow(hwnd, dc.ToInt64(), 2)
    finally:
        graphics.ReleaseHdc(dc)
        graphics.Dispose()
    crop = bitmap.Clone(Rectangle(bitmap.Width-240, 0, 240, 90), bitmap.PixelFormat)
    crop.Save(str(directory.parent / 'window-native-close-hover.png'), ImageFormat.Png)
    crop.Dispose()
    bitmap.Dispose()
    raise SystemExit()
scale = user32.GetDpiForWindow(hwnd) / 96
work_area = wintypes.RECT()
assert user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0)
width, height = round(1100*scale)+80, round(700*scale)+80
try:
    for name, hit, horizontal, vertical in [
        ('left', 10, -1, 0), ('right', 11, 1, 0),
        ('top', 12, 0, -1), ('bottom', 15, 0, 1),
        ('top-left', 13, -1, -1), ('top-right', 14, 1, -1),
        ('bottom-left', 16, -1, 1), ('bottom-right', 17, 1, 1),
    ]:
        # Keep the edge under test on screen even if the app's minimum height
        # exceeds the available work area at the test machine's display scale.
        x_origin = work_area.right-width-60 if horizontal > 0 else work_area.left+60
        y_origin = work_area.bottom-height-60 if vertical > 0 else work_area.top+60
        place((x_origin, y_origin, x_origin+width, y_origin+height))
        left, top, right, bottom = rectangle()
        x = left+1 if horizontal < 0 else right-2 if horizontal > 0 else (left+right)//2
        y = top+1 if vertical < 0 else bottom-2 if vertical > 0 else (top+bottom)//2
        position = ((y & 0xffff) << 16) | (x & 0xffff)
        actual_hit = user32.SendMessageW(hwnd, 0x0084, 0, position)
        assert actual_hit == hit, (name, 'incorrect native hit test', actual_hit)
        # Enter Windows' own sizing loop and address its keyboard messages to
        # this HWND only; remote desktop pointer updates cannot move the target.
        assert user32.PostMessageW(hwnd, 0x0112, 0xF000+(hit-9), 0)
        for step in range(6):
            if horizontal:
                user32.PostMessageW(hwnd, 0x0100, 0x25 if horizontal < 0 else 0x27, 0)
            if vertical:
                user32.PostMessageW(hwnd, 0x0100, 0x26 if vertical < 0 else 0x28, 0)
        user32.PostMessageW(hwnd, 0x0100, 0x0D, 0)
        time.sleep(.2)
        new_left, new_top, new_right, new_bottom = rectangle()
        if horizontal:
            assert new_right-new_left > right-left+20, (name, 'width did not change', rectangle(), (left, top, right, bottom))
        if vertical:
            assert new_bottom-new_top > bottom-top+20, (name, 'height did not change', rectangle(), (left, top, right, bottom))
    print('PASS native sizing loop and hit testing on all four edges and corners; system title bar remains hidden')
finally:
    user32.PostMessageW(hwnd, 0x0100, 0x1B, 0)
    place(original)
