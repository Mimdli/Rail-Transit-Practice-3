"""网络模块回环测试 — 无需外部硬件，在 localhost 上验证收发。

运行方式：
  py -3.13 -m pytest tests/test_network_loopback.py -v

测试内容：
  1. codec 编解码正确性
  2. 车辆 UDP 发送/接收
  3. 信号网关 UDP 发送
  4. 视景 UDP 发送  
  5. 司机台 TCP 发送
  6. PLC TCP 接收
"""

import socket
import struct
import time
import logging
from unittest.mock import patch

logging.basicConfig(level=logging.WARNING)
logging.getLogger("src.network").setLevel(logging.WARNING)

import pytest

import src.network.codec as codec


def free_udp() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p

def free_tcp() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ============================================================
# 第一组：codec 纯函数测试
# ============================================================

class TestCodec:
    def test_vehicle_pack_unpack(self):
        trains = [(1.5, 22.3, 1000.0), (-0.5, 15.0, 500.0)]
        data = codec.pack_vehicle_udp(trains)
        assert len(data) == 480
        out = codec.unpack_vehicle_udp(data)
        assert len(out) == 20
        assert abs(out[0][0] - 1.5) < 0.001

    def test_signal_switch_signal(self):
        data = codec.pack_signal_switch_signal([(1, 0x01)], [(101, 0x04)])
        assert len(data) > 12

    def test_atp_output(self):
        data = codec.pack_signal_atp_output(1, 0xFF, 0x00, 0x01)
        assert len(data) > 16

    def test_plc_unpack(self):
        buf = bytearray(46)
        # 兼容字段 handle_position 对应牵引级位 WORD8，即整包偏移 40。
        struct.pack_into("<H", buf, 40, 0x0045)
        # 协议定义 WORD6 为枚举：0=零位、1=向前、2=向后。
        struct.pack_into("<H", buf, 36, 0x0001)
        result = codec.unpack_plc_data(bytes(buf))
        assert result is not None
        assert result["handle_position"] == 0x45
        assert result["dir_forward"] is True

    def test_plc_unpack_short(self):
        assert codec.unpack_plc_data(b"\x00" * 30) is None

    def test_vision_minimal(self):
        data = codec.pack_vision_tcms2view(
            live_counter=1, signal_states=[0x01], switch_states=[0x01],
            speed_mms=15000, accel_pct=50, run_state=0x13,
            position_mm=100000, edge_id=5, direction=1,
        )
        assert len(data) == 1280

    def test_vision_with_others(self):
        data = codec.pack_vision_tcms2view(
            live_counter=2, signal_states=[], switch_states=[],
            speed_mms=0, accel_pct=0, run_state=0,
            position_mm=0, edge_id=0, direction=1,
            other_trains=[{"dist": 1000, "edge": 3, "dir": 1, "speed_cms": 2000}],
        )
        assert len(data) == 1280

    def test_signal_screen(self):
        data = codec.pack_signal_screen(
            speed=15.5, acceleration=0.5, speed_limit=22.0,
            mode=3, curr_station=2, next_station=3, next_station_dist=500.0,
        )
        assert len(data) == 68
        assert abs(struct.unpack_from("<f", data, 44)[0] - 15.5) < 0.01

    def test_network_screen(self):
        data = codec.pack_network_screen(
            speed=12.0, acceleration=-0.3, speed_limit=20.0,
            run_mode=2, door_states=[1, 0, 0, 0, 0, 0],
        )
        assert len(data) == 572
        assert abs(struct.unpack_from("<f", data, 40)[0] - 12.0) < 0.01


# ============================================================
# 第二组：网络收发测试
# ============================================================

def test_vehicle_udp_loopback():
    """车辆 UDP：发送数据到模拟总控 + 接收模拟总控控制指令"""
    # 模拟总控接收端
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    remote_port = recv_sock.getsockname()[1]
    recv_sock.settimeout(2.0)

    # 模拟总控发指令的端口（本机收）
    local_port = free_udp()
    # cmd_sock 在 local_port 上发送指令
    cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cmd_sock.bind(("127.0.0.1", 0))
    # cmd_sock 的本地端口无所谓，发给 local_port 就行

    from src.network.udp_vehicle import VehicleUDPClient

    patchers = [
        patch("src.network.udp_vehicle.VEHICLE_UDP_LOCAL_ADDR", "127.0.0.1"),
        patch("src.network.udp_vehicle.VEHICLE_UDP_REMOTE_ADDR", "127.0.0.1"),
        patch("src.network.udp_vehicle.VEHICLE_UDP_LOCAL_PORT", local_port),
        patch("src.network.udp_vehicle.VEHICLE_UDP_REMOTE_PORT", remote_port),
        patch("src.network.udp_vehicle.VEHICLE_UDP_CYCLE_MS", 100),
        patch("src.network.udp_vehicle.VEHICLE_UDP_SEND_SIZE", 480),
        patch("src.network.udp_vehicle.VEHICLE_UDP_RECV_SIZE", 320),
    ]
    for p in patchers:
        p.start()

    received_packets = []
    received_cmds = []

    def source():
        return [(2.0, 30.0, 5000.0)] + [(0, 0, 0)] * 19
    def on_cmd(cmds):
        received_cmds.append(cmds)

    try:
        client = VehicleUDPClient()
        client.set_send_source(source)
        client.set_recv_callback(on_cmd)
        client.start()
        time.sleep(0.4)

        # 验证发送
        try:
            data, addr = recv_sock.recvfrom(480)
            received_packets.append(data)
        except socket.timeout:
            pass
        assert len(received_packets) == 1, "未收到车辆 UDP 数据"
        assert len(received_packets[0]) == 480
        trains = codec.unpack_vehicle_udp(received_packets[0])
        assert abs(trains[0][0] - 2.0) < 0.001

        # 模拟总控发送控制指令
        cmd_data = struct.pack("<dd", 1.0, 60.0) * 20
        cmd_sock.sendto(cmd_data, ("127.0.0.1", local_port))
        time.sleep(0.3)

        assert len(received_cmds) > 0, "未收到控制指令回调"
    finally:
        client.stop()
        for p in patchers:
            p.stop()
        recv_sock.close()
        cmd_sock.close()


def test_signal_gateway_loopback():
    """信号网关 UDP 发送"""
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    remote_port = recv_sock.getsockname()[1]
    recv_sock.settimeout(2.0)

    from src.network.signal_gateway import SignalGateway

    patchers = [
        patch("src.network.signal_gateway.SIGNAL_GATEWAY_ADDR", "127.0.0.1"),
        patch("src.network.signal_gateway.SIGNAL_GATEWAY_PORT", remote_port),
        patch("src.network.signal_gateway.SIGNAL_LOCAL_PORT", 0),
        patch("src.network.signal_gateway.SIGNAL_GATEWAY_CYCLE_MS", 100),
    ]
    for p in patchers:
        p.start()

    received = []
    def source():
        return ([(1, 0x01)], [(101, 0x04)])

    try:
        gw = SignalGateway()
        gw.set_send_source(source)
        gw.start()
        time.sleep(0.4)
        try:
            data, addr = recv_sock.recvfrom(4096)
            received.append(data)
        except socket.timeout:
            pass
        assert len(received) == 1, "未收到信号网关数据"
    finally:
        gw.stop()
        for p in patchers:
            p.stop()
        recv_sock.close()


def test_vision_udp_loopback():
    """视景 UDP 发送"""
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    remote_port = recv_sock.getsockname()[1]
    recv_sock.settimeout(2.0)

    from src.network.vision_udp import VisionUDPClient

    patchers = [
        patch("src.network.vision_udp.VISION_LOCAL_ADDR", "127.0.0.1"),
        patch("src.network.vision_udp.VISION_REMOTE_ADDR", "127.0.0.1"),
        patch("src.network.vision_udp.VISION_REMOTE_PORT", remote_port),
        patch("src.network.vision_udp.VISION_CYCLE_MS", 100),
    ]
    for p in patchers:
        p.start()

    received = []
    def source():
        return {"signal_states": [0x01], "switch_states": [0x01],
                "speed_mms": 12000, "accel_pct": 30,
                "position_mm": 50000, "edge_id": 3, "direction": 1,
                "run_state": 0x13}

    try:
        client = VisionUDPClient()
        client.set_data_source(source)
        client.start()
        time.sleep(0.4)
        try:
            data, addr = recv_sock.recvfrom(2000)
            received.append(data)
        except socket.timeout:
            pass
        assert len(received) == 1, "未收到视景 UDP 数据"
        assert len(received[0]) == 1280
    finally:
        client.stop()
        for p in patchers:
            p.stop()
        recv_sock.close()


def test_cab_display_tcp_loopback():
    """司机台 TCP 发送（网络屏+信号屏）"""
    net_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    net_server.bind(("127.0.0.1", 0))
    net_port = net_server.getsockname()[1]
    net_server.listen(1)
    net_server.settimeout(2.0)

    sig_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sig_server.bind(("127.0.0.1", 0))
    sig_port = sig_server.getsockname()[1]
    sig_server.listen(1)
    sig_server.settimeout(2.0)

    from src.network.cab_display import CabDisplayClient

    patchers = [
        patch("src.network.cab_display.CAB_DISPLAY_ADDR", "127.0.0.1"),
        patch("src.network.cab_display.CAB_NETWORK_SCREEN_PORT", net_port),
        patch("src.network.cab_display.CAB_SIGNAL_SCREEN_PORT", sig_port),
        patch("src.network.cab_display.CAB_DISPLAY_CYCLE_MS", 100),
    ]
    for p in patchers:
        p.start()

    net_data, sig_data = [], []

    def accept_one(srv, buf):
        try:
            conn, addr = srv.accept()
            d = conn.recv(600)
            buf.append((d, len(d)))
            conn.close()
        except socket.timeout:
            pass

    try:
        client = CabDisplayClient()
        client.set_network_data_source(
            lambda: {"speed": 10.0, "acceleration": 0.0, "speed_limit": 20.0,
                     "run_mode": 2, "run_dir": 0})
        client.set_signal_data_source(
            lambda: {"speed": 10.0, "acceleration": 0.0, "speed_limit": 20.0,
                     "mode": 3})
        client.start()
        time.sleep(0.5)
        accept_one(net_server, net_data)
        accept_one(sig_server, sig_data)

        assert len(net_data) == 1, "未收到网络屏 TCP 数据"
        assert net_data[0][1] == 572
        assert len(sig_data) >= 1, "未收到信号屏 TCP 数据"
        assert sig_data[0][1] == 68
    finally:
        client.stop()
        for p in patchers:
            p.stop()
        net_server.close()
        sig_server.close()


def test_plc_receive():
    """PLC 接收：启动 TCP 模拟 PLC 并发送 46B 报文"""
    port = free_tcp()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    server.settimeout(5.0)

    from src.network import tcp_plc as plc_mod
    from src.network.tcp_plc import PLCClient

    # 直接修改模块属性（比 patch 更可靠）
    orig_p1, orig_p2, orig_p3 = plc_mod.PLC_PORT_1, plc_mod.PLC_PORT_2, plc_mod.PLC_PORT_3
    plc_mod.PLC_PORT_1 = port
    plc_mod.PLC_PORT_2 = 0  # 0 = 跳过该端口
    plc_mod.PLC_PORT_3 = 0
    plc_mod.PLC_SERVER_ADDR = "127.0.0.1"
    plc_mod.PLC_CYCLE_MS = 100

    received_data = []
    def cb(data):
        received_data.append(data)

    try:
        client = PLCClient()
        client.set_recv_callback(cb)
        client.start()
        time.sleep(1.0)

        conn, addr = server.accept()
        buf = bytearray(46)
        # 兼容字段 handle_position 对应牵引级位 WORD8，即整包偏移 40。
        struct.pack_into("<H", buf, 40, 0x0045)
        # 协议定义 WORD6 为枚举：0=零位、1=向前、2=向后。
        struct.pack_into("<H", buf, 36, 0x0001)
        conn.sendall(bytes(buf))
        time.sleep(0.4)

        assert len(received_data) > 0, "未收到 PLC 数据"
        assert received_data[0]["handle_position"] == 0x45
        assert received_data[0]["dir_forward"] is True
        conn.close()
    finally:
        client.stop()
        plc_mod.PLC_PORT_1, plc_mod.PLC_PORT_2, plc_mod.PLC_PORT_3 = orig_p1, orig_p2, orig_p3
        plc_mod.PLC_SERVER_ADDR = "192.168.100.123"
        plc_mod.PLC_CYCLE_MS = 100
        server.close()
