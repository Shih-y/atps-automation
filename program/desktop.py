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
        window = webview.create_window(WINDOW_TITLE, url, width=1440, height=960,
                                       min_size=(1100, 700))
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
