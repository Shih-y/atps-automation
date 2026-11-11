"""List OS serial-port metadata without opening ports or transmitting commands."""
import re
import sys
from pathlib import Path


def scan_serial_ports():
    vendor = Path(__file__).with_name('vendor')
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    try:
        from serial.tools import list_ports
        devices = list(list_ports.comports())
    except Exception as exc:
        raise ValueError(f'串口扫描失败：{exc}。请检查 CH340 驱动或手动填写串口。') from exc

    ports = []
    for item in devices:
        device = str(item.device or '').strip()
        if not device:
            continue
        description = str(item.description or '')
        hwid = str(item.hwid or '')
        vid, pid = getattr(item, 'vid', None), getattr(item, 'pid', None)
        # Some drivers expose identifiers only in the hardware-ID string.
        identifiers = re.search(r'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})', hwid, re.I)
        if identifiers is None:
            identifiers = re.search(r'VID:PID=([0-9A-F]{4}):([0-9A-F]{4})', hwid, re.I)
        if identifiers:
            if vid is None: vid = int(identifiers[1], 16)
            if pid is None: pid = int(identifiers[2], 16)
        known_id = vid == 0x1A86 and pid in (0x7523, 0x5523)
        named_chip = re.search(r'(?<![A-Z0-9])CH34[01][A-Z]?(?![A-Z0-9])',
                               description + ' ' + hwid, re.I) is not None
        ports.append(dict(device=device, description=description, hwid=hwid,
                          vid=vid, pid=pid, is_ch340=known_id or named_chip))
    ports.sort(key=lambda p: [int(part) if part.isdigit() else part.casefold()
                              for part in re.split(r'(\d+)', p['device'])])
    ch340 = [item for item in ports if item['is_ch340']]
    suggested = ch340[0]['device'] if len(ch340) == 1 else None
    if suggested:
        message = f'发现 CH340/CH341：{suggested}。点击连接时会重新扫描；扫描不会打开串口。'
    elif ch340:
        message = '发现多个 CH340/CH341（' + '、'.join(p['device'] for p in ch340) + '），请手动选择串口。'
    else:
        message = '未发现 CH340/CH341。请检查 USB 连接和驱动，或手动选择已确认的串口。'
    return dict(ports=ports, ch340_ports=ch340, suggested_port=suggested, message=message)
