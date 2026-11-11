"""TEST ONLY: injects a hardware double; never opens a serial port."""
from pathlib import Path
import json,sys,tempfile
P=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(P));sys.path.insert(0,str(P/'tests'))
from controller import Controller
from server import Server
from fakes import DemoHardware
from test_support import install_test_profiles
import controller,server
def fake_scan_serial_ports():
    port={'device':'COM6','description':'TEST ONLY USB-SERIAL CH340','hwid':'USB VID:PID=1A86:7523','vid':0x1A86,'pid':0x7523,'is_ch340':True}
    return {'ports':[port],'ch340_ports':[port],'suggested_port':'COM6','message':'TEST ONLY fake OS enumeration; no serial port opened'}
controller.scan_serial_ports=fake_scan_serial_ports
server.scan_serial_ports=fake_scan_serial_ports
OUT=Path(__file__).parent/'test_output';OUT.mkdir(exist_ok=True)
c=Controller(tempfile.mkdtemp(prefix='browser_',dir=OUT),hardware_factory=lambda cfg:DemoHardware(cfg))
install_test_profiles(c)
(OUT/'fixture_config.json').write_text(json.dumps(c.config,ensure_ascii=False),encoding='utf-8')
c.config=json.loads((P/'config.default.json').read_text(encoding='utf-8'))
s=Server(('127.0.0.1',8879),c)
print('TEST ONLY injected hardware on 127.0.0.1:8879',flush=True)
try:s.serve_forever()
finally:c.disconnect();s.server_close()
