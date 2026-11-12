"""Loopback-only local control. No remote bind, no serial probing, real controls, no startup checklist."""
import argparse
import json
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from controller import Controller
from capture_store import MAX_REQUEST_BYTES
from serial_discovery import scan_serial_ports

class Server(ThreadingHTTPServer):
    daemon_threads=True
    # HTTPServer defaults to SO_REUSEADDR, which permits duplicate listeners on
    # Windows. Different controllers can otherwise receive adjacent requests.
    allow_reuse_address = sys.platform != 'win32'
    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()
    def __init__(self,address,controller):
        if address[0] != '127.0.0.1': raise ValueError('只能绑定127.0.0.1')
        self.controller=controller; self.token=secrets.token_urlsafe(32)
        self.static=Path(__file__).with_name('static')
        super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,value,status=200,mime='application/json; charset=utf-8'):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(raw)

    def trusted(self,mutation=False):
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if host not in allowed: raise ValueError('Host不允许')
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://{h}' for h in allowed}: raise ValueError('Origin不允许')
        if self.headers.get('Sec-Fetch-Site')=='cross-site': raise ValueError('不接受跨站请求')
        if mutation and not secrets.compare_digest(self.headers.get('X-Control-Token',''),self.server.token): raise ValueError('缺少本地控制令牌，请刷新页面')

    def do_GET(self):
        try:
            self.trusted(); path=urlsplit(self.path).path; c=self.server.controller
            if path=='/api/token': return self.reply({'token':self.server.token})
            if path=='/api/state': return self.reply(c.snapshot())
            if path=='/api/config': return self.reply(c.snapshot()['config'])
            if path=='/api/serial-ports': return self.reply(scan_serial_ports())
            if path.startswith('/api/images/'):
                parts=path.split('/')
                if len(parts)!=5: raise ValueError('图片路径无效')
                return self.reply(c.capture.image_path(parts[3],parts[4]).read_bytes(),mime='image/png')
            if path in ('/','/index.html'): return self.reply((self.server.static/'index.html').read_bytes(),mime='text/html; charset=utf-8')
            if path in ('/app.js','/bench.js','/live.js','/tubes.js','/free.js','/style.css','/console.js','/console.css','/phase_analysis.js'):
                mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8'
                return self.reply((self.server.static/path[1:]).read_bytes(),mime=mime)
            return self.reply({'ok':False,'error':'未找到'},404)
        except (ValueError,FileNotFoundError) as e: self.reply({'ok':False,'error':str(e)},400)
        except Exception as e: self.reply({'ok':False,'error':str(e)},500)

    def do_POST(self):
        try:
            if self.headers.get('Transfer-Encoding'): raise ValueError('不接受分块请求')
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json': raise ValueError('仅接受JSON')
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=MAX_REQUEST_BYTES: raise ValueError('请求过大或为空')
            self.connection.settimeout(10)
            raw=self.rfile.read(size)
            # Consume the bounded request before rejecting auth; otherwise Windows
            # may reset a connection with unread input and hide the error response.
            self.trusted(True)
            def no_constant(x): raise ValueError('不接受NaN/Infinity')
            body=json.loads(raw,parse_constant=no_constant)
            if not isinstance(body,dict): raise ValueError('请求须为对象')
            c=self.server.controller; path=urlsplit(self.path).path
            if path=='/api/connect': c.connect(body.get('mode','real'),body.get('pump_port',body.get('port','')),body.get('valve_port',body.get('port','')),auto_detect=body.get('auto_detect',False))
            elif path=='/api/disconnect': c.disconnect()
            elif path=='/api/stop': c.stop_keep_connection() if c.free_debug else c.stop()
            elif path=='/api/config': c.configure(body.get('config'))
            elif path=='/api/calibration/report': return self.reply(c.calibration_report(body))
            elif path=='/api/bench/report': return self.reply(c.bench_report(body))
            elif path=='/api/reference': return self.reply(c.update_dose_reference(body))
            elif path=='/api/imaging/begin': return self.reply(c.begin_imaging(body))
            elif path=='/api/action': c.start(body)
            elif path=='/api/ack': c.acknowledge(body)
            elif path=='/api/capture': return self.reply(c.capture_frame(body))
            elif path=='/api/label': c.label(body)
            elif path=='/api/discard':
                with c.lock:
                    if c.busy: raise ValueError('操作中不能放弃图像')
                    c.capture.discard(body.get('batch_id'))
            else: return self.reply({'ok':False,'error':'未找到'},404)
            self.reply({'ok':True,'state':c.snapshot()})
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e: self.reply({'ok':False,'error':str(e)},400)
        except Exception as e: self.reply({'ok':False,'error':str(e)},500)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8765); p.add_argument('--data-dir',default=str(Path(__file__).with_name('data'))); p.add_argument('--open-browser',action='store_true')
    args=p.parse_args(); controller=Controller(args.data_dir); server=Server(('127.0.0.1',args.port),controller)
    url=f'http://127.0.0.1:{server.server_port}'
    print('CH340 实机实验控制器（点击连接后才打开串口）:',url,flush=True)
    if args.open_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        controller.disconnect(); server.server_close()

if __name__=='__main__': main()
