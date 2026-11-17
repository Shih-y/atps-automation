"""Desktop entry point: loopback control server plus a pywebview/WebView2 window.

The packaged executable stays a thin shell around server.py. All control,
validation and persistence remain in the existing localhost service; the
window only renders the same pages a browser would.
"""
import argparse
import functools
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from controller import Controller
from server import Server

APP_NAME = 'ATPS-Automation'
WINDOW_TITLE = '双水相配液与图像记录平台'


class WindowControls:
    """Expose only shell actions; closing still runs main's shutdown cleanup."""

    def __init__(self):
        self._window = None
        self._maximized = False

    def _bind(self, window):
        self._window = window
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored

    def _on_maximized(self):
        self._maximized = True

    def _on_restored(self):
        self._maximized = False

    def window_state(self):
        return {'maximized': self._maximized}

    def minimize(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._maximized:
            self._window.restore()
            self._maximized = False
        else:
            self._window.maximize()
            self._maximized = True
        return self.window_state()

    def close(self):
        self._window.destroy()


def enable_window_resize(window):
    """Keep native sizing while extending the page through the top frame.

    pywebview's WinForms frameless mode removes WS_THICKFRAME as well as the
    caption. Restore only the sizing frame so Windows handles edges, corners,
    minimum sizes and resize cursors, rather than resizing through JavaScript.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    hwnd = window.native.Handle.ToInt64()
    comctl32 = ctypes.WinDLL('comctl32', use_last_error=True)
    subclass_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM,
                                      ctypes.c_size_t, ctypes.c_size_t)
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, subclass_type,
                                         ctypes.c_size_t, ctypes.c_size_t]
    comctl32.SetWindowSubclass.restype = wintypes.BOOL
    comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, subclass_type, ctypes.c_size_t]
    comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    comctl32.DefSubclassProc.restype = ctypes.c_ssize_t

    @subclass_type
    def frame_proc(handle, message, wparam, lparam, subclass_id, reference):
        try:
            if message == 0x0083 and lparam:  # WM_NCCALCSIZE
                rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
                proposed_top = rect.top
                result = comctl32.DefSubclassProc(handle, message, wparam, lparam)
                if not user32.IsZoomed(handle):
                    # Keep the native side/bottom borders but eliminate the
                    # blank top strip introduced by WS_THICKFRAME.
                    rect.top = proposed_top
                return result
            if message == 0x0084 and not user32.IsZoomed(handle):  # WM_NCHITTEST
                rect = wintypes.RECT()
                user32.GetWindowRect(handle, ctypes.byref(rect))
                x = ctypes.c_short(lparam & 0xffff).value
                y = ctypes.c_short((lparam >> 16) & 0xffff).value
                edge = max(4, round(8 * user32.GetDpiForWindow(handle) / 96))
                if rect.top <= y < rect.top + edge:
                    if rect.left <= x < rect.left + edge:
                        return 13  # HTTOPLEFT
                    if rect.right - edge <= x < rect.right:
                        return 14  # HTTOPRIGHT
                    if rect.left <= x < rect.right:
                        return 12  # HTTOP
            if message == 0x0082:  # WM_NCDESTROY
                comctl32.RemoveWindowSubclass(handle, frame_proc, subclass_id)
        except Exception:
            logging.exception('custom window frame message failed')
        return comctl32.DefSubclassProc(handle, message, wparam, lparam)

    # before_show runs on the owning UI thread. Retain the callback until the
    # HWND is destroyed so ctypes cannot collect a live native function pointer.
    window._native_frame_callback = frame_proc
    if not comctl32.SetWindowSubclass(hwnd, frame_proc, 1, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    style = user32.GetWindowLongPtrW(hwnd, -16)  # GWL_STYLE
    ctypes.set_last_error(0)
    previous = user32.SetWindowLongPtrW(hwnd, -16, (style | 0x00040000) & ~0x00C00000)
    if not previous and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    # Refresh cached non-client geometry without moving, resizing or activating.
    if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037):
        raise ctypes.WinError(ctypes.get_last_error())


def default_data_dir():
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if base:
        return Path(base) / APP_NAME / 'data'
    return Path.home() / f'.{APP_NAME.lower()}' / 'data'


def report_fatal(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
    except Exception:
        pass


def configure_logging(data_dir, port):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(data_dir / 'desktop.log', maxBytes=1_000_000,
                                  backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logger.addHandler(handler)
    logging.info('desktop start pid=%s frozen=%s port=%s', os.getpid(),
                 bool(getattr(sys, 'frozen', False)), port)
    return logger


def shutdown(controller, server, thread):
    """Release hardware before storage/logging; attempt every cleanup stage."""
    failures = []
    for name, operation in [('disconnect', controller.disconnect),
                            ('http shutdown', server.shutdown),
                            ('archive and close', server.server_close),
                            ('http thread join', lambda: thread.join(3))]:
        try:
            operation()
        except Exception as error:
            failures.append((name, str(error)))
    try:
        logging.info('desktop shutdown; cleanup errors=%s', failures)
    except Exception:
        pass


def allow_camera(window, origin):
    """Grant WebView2 camera access for the app's exact loopback origin.

    WebView2 denies getUserMedia without a PermissionRequested handler, and the
    embedded window has no permission prompt UI. Any other origin is ignored.
    """
    try:
        import clr
        from webview.util import interop_dll_path
        clr.AddReference(interop_dll_path('Microsoft.Web.WebView2.Core.dll'))
        from Microsoft.Web.WebView2.Core import (CoreWebView2PermissionKind,
                                                 CoreWebView2PermissionState)
    except Exception:
        logging.exception('WebView2 permission bridge unavailable; camera may stay blocked')
        return

    def on_permission(sender, args):
        try:
            uri = urlsplit(str(args.Uri))
            requested = f'{uri.scheme}://{uri.netloc}'
            if args.PermissionKind == CoreWebView2PermissionKind.Camera and requested == origin:
                args.State = CoreWebView2PermissionState.Allow
                args.SavesInProfile = True
                args.Handled = True
                logging.info('camera permission granted for %s', args.Uri)
        except Exception:
            logging.exception('camera permission handler failed')

    def attach(core):
        core.PermissionRequested += on_permission

    def on_initialized(sender, args):
        if args.IsSuccess:
            attach(sender.CoreWebView2)
        else:
            logging.error('WebView2 initialization failed: %s', args.InitializationException)

    control = getattr(getattr(window, 'native', None), 'webview', None)
    if control is None:
        logging.error('native WebView2 control unavailable; camera permission not installed')
        return
    if control.CoreWebView2 is not None:
        attach(control.CoreWebView2)
    else:
        control.CoreWebView2InitializationCompleted += on_initialized


def main():
    parser = argparse.ArgumentParser(description='ATPS desktop shell')
    parser.add_argument('--port', type=int, default=8765, help='loopback port (0 chooses a free port)')
    parser.add_argument('--data-dir', default=str(default_data_dir()),
                        help='persistent data directory')
    parser.add_argument('--debug-port', type=int, default=None,
                        help='developer only: WebView2 remote debugging port')
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report_fatal(f'无法创建数据目录：{data_dir}\n{exc}')
        return 1

    configure_logging(data_dir, args.port)
    try:
        controller = Controller(str(data_dir))
    except Exception as exc:
        logging.exception('controller startup failed')
        report_fatal(f'控制器启动失败：{exc}\n\n数据目录：{data_dir}')
        return 1

    try:
        server = Server(('127.0.0.1', args.port), controller)
    except OSError as exc:
        logging.exception('server bind failed')
        report_fatal(f'无法监听本机端口 {args.port}：{exc}\n'
                     '如果程序已经在运行，请先关闭原窗口；也可以稍后重试。')
        return 1

    thread = threading.Thread(target=server.serve_forever, name='atps-http', daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    logging.info('serving %s with data %s', url, data_dir)

    try:
        import webview
        webview.settings['REMOTE_DEBUGGING_PORT'] = args.debug_port
        controls = WindowControls()
        window = webview.create_window(WINDOW_TITLE, url, width=1440, height=960,
                                       min_size=(1100, 700), resizable=True, frameless=True,
                                       easy_drag=False, js_api=controls)
        controls._bind(window)
        window.events.before_show += enable_window_resize
        window.events.before_show += functools.partial(allow_camera, origin=url)
        webview.start(gui='edgechromium', debug=args.debug_port is not None)
    except Exception as exc:
        logging.exception('desktop window failed')
        report_fatal(f'桌面窗口启动失败：{exc}\n\n日志：{data_dir / "desktop.log"}')
        return 1
    finally:
        shutdown(controller, server, thread)
    return 0


if __name__ == '__main__':
    sys.exit(main())
