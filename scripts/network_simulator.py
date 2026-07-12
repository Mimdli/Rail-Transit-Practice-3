"""网络通信模拟器 — 模拟总控系统，在 localhost 上测试所有协议收发。

用法：
  py -3.13 scripts/network_simulator.py

运行后该脚本会：
  1. 在 localhost 上启动 5 个接收端（模拟总控/PLC）
  2. 启动 NetworkManager，设置模拟数据源
  3. 每 2 秒打印一次所有模块的收发状态

可在另一终端同时运行仿真程序，切换到「外部系统」模式来对接。
按 Ctrl+C 退出。
"""

import socket
import struct
import threading
import time
import logging
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# 关掉网络模块自身的 debug 日志以免刷屏
logging.getLogger("src.network").setLevel(logging.WARNING)
logging.getLogger("NetworkSim").setLevel(logging.INFO)
log = logging.getLogger("NetworkSim")


# ============================================================
# 总控模拟器：启动 UDP/TCP 接收端，打印收到的数据概要
# ============================================================

class SimReceiver:
    """通用接收器基类，统计收包数和字节数"""

    def __init__(self, name: str):
        self.name = name
        self.packet_count = 0
        self.byte_count = 0
        self.last_data: Optional[bytes] = None
        self._running = True

    def stop(self):
        self._running = False


class UDPReceiver(SimReceiver):
    """UDP 接收端"""

    def __init__(self, name: str, port: int, recv_size: int = 4096):
        super().__init__(name)
        self.port = port
        self.recv_size = recv_size
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"SimUDP-{name}")
        self._thread.start()

    def _run(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(self.recv_size)
                self.packet_count += 1
                self.byte_count += len(data)
                self.last_data = data
                self._print_summary(data, addr)
            except socket.timeout:
                pass

    def _print_summary(self, data: bytes, addr: tuple):
        """打印数据概要"""
        log.info("[%s]  收 %d bytes 来自 %s:%d",
                 self.name, len(data), *addr)
        ShowHex(data, max_bytes=16)


class TCPServer(SimReceiver):
    """TCP 服务器端（模拟总控接收）"""

    def __init__(self, name: str, port: int, recv_size: int = 600):
        super().__init__(name)
        self.port = port
        self.recv_size = recv_size
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", port))
        self.server.listen(1)
        self.server.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"SimTCP-{name}")
        self._thread.start()

    def _run(self):
        while self._running:
            try:
                conn, addr = self.server.accept()
                data = conn.recv(self.recv_size)
                if data:
                    self.packet_count += 1
                    self.byte_count += len(data)
                    self.last_data = data
                    log.info("[%s]  收 %d bytes 来自 %s:%d",
                             self.name, len(data), *addr)
                    ShowHex(data, max_bytes=16)
                conn.close()
            except socket.timeout:
                pass

    def stop(self):
        super().stop()
        try:
            self.server.close()
        except OSError:
            pass


class PLCSimulator(SimReceiver):
    """PLC 模拟器：启动 TCP 服务器，周期发送 46B PLC 报文"""

    def __init__(self, port: int):
        super().__init__("PLC模拟台")
        self.port = port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", port))
        self.server.listen(1)
        self.server.settimeout(0.5)
        self._handle_value = 0
        self._connected = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="SimPLC")
        self._thread.start()

    def _build_plc_packet(self, handle: int) -> bytes:
        """构造 46B PLC 报文"""
        buf = bytearray(46)
        struct.pack_into("<H", buf, 24, handle)  # 手柄级位
        struct.pack_into("<H", buf, 26, 0x0001)  # 向前
        struct.pack_into("<H", buf, 28, 0x0000)  # 门控
        return bytes(buf)

    def _run(self):
        while self._running:
            try:
                conn, addr = self.server.accept()
                log.info("[PLC模拟台]  客户端已连接 %s:%d", *addr)
                self._connected = True
                while self._running and self._connected:
                    # 周期发送 PLC 数据（模拟实际 PLC 100ms 一帧）
                    self._handle_value = (self._handle_value + 1) % 128
                    data = self._build_plc_packet(self._handle_value)
                    try:
                        conn.sendall(data)
                    except (ConnectionResetError, BrokenPipeError):
                        self._connected = False
                        break
                    time.sleep(0.1)
                conn.close()
            except socket.timeout:
                pass

    def stop(self):
        super().stop()
        self._connected = False
        try:
            self.server.close()
        except OSError:
            pass


def ShowHex(data: bytes, max_bytes: int = 32):
    """打印字节的前几个字节为 hex"""
    show = data[:max_bytes]
    hex_str = " ".join(f"{b:02X}" for b in show)
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in show)
    if len(data) > max_bytes:
        hex_str += " ..."
        ascii_str += " ..."
    print(f"          {hex_str}")
    print(f"          {ascii_str}")


# ============================================================
# 模拟数据源：产生假数据供 NetworkManager 发送
# ============================================================

class MockDataSource:
    """产生模拟仿真数据"""

    def __init__(self):
        self._counter = 0

    # 车辆数据
    def vehicle_data(self) -> list[tuple[float, float, float]]:
        self._counter += 1
        speed = 15.0 + (self._counter % 100) * 0.1
        accel = 0.5
        dist = 5000.0 + self._counter * 0.5
        trains = [(accel, speed, dist)]
        return trains + [(0, 0, 0)] * 19  # 填充到 20 车

    # 信号/道岔数据
    def signal_data(self):
        switches = [(1, 0x01), (2, 0x02), (3, 0x01)]
        signals = [(101, 0x01), (102, 0x04), (103, 0x03)]
        return (switches, signals)

    # 视景数据
    def vision_data(self) -> dict:
        self._counter += 1
        return {
            "signal_states": [0x01, 0x04, 0x02, 0x01],
            "switch_states": [0x01, 0x02],
            "speed_mms": int(15000 * 1000),
            "accel_pct": 30,
            "run_state": 0x13,
            "position_mm": 5000000,
            "edge_id": 5,
            "direction": 1,
        }

    # 司机台网络屏数据
    def cab_network_data(self) -> dict:
        return {
            "speed": 12.5,
            "acceleration": 0.3,
            "speed_limit": 22.0,
            "run_mode": 2,
            "run_dir": 0,
            "power_pull": 350,
            "net_pressure": 750,
            "curr_station": 2,
            "next_station": 3,
            "end_station": 10,
            "power_state": 0,
            "door_states": [0, 0, 0, 0, 0, 0],
            "has_power": True,
        }

    # 司机台信号屏数据
    def cab_signal_data(self) -> dict:
        return {
            "speed": 12.5,
            "acceleration": 0.3,
            "speed_limit": 22.0,
            "mode": 5,
            "run_dir": 0,
            "curr_station": 2,
            "next_station": 3,
            "end_station": 10,
            "next_station_dist": 500.0,
        }


# ============================================================
# 主流程
# ============================================================

def find_free_ports(count: int) -> list[int]:
    """找多个空闲端口"""
    socks = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
             for _ in range(count)]
    ports = []
    for s in socks:
        s.bind(("127.0.0.1", 0))
        ports.append(s.getsockname()[1])
        s.close()
    return ports


def main():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    # ── 1. 找空闲端口 ───────────────────────────────
    print("=" * 60)
    print("  轨道交通多系统通信模拟器")
    print("=" * 60)
    print()
    print("正在分配本地端口...")

    ports = find_free_ports(6)
    port_vehicle_send = ports[0]   # 模拟总控收车辆数据
    port_vehicle_recv = ports[1]   # 模拟总控发控制指令
    port_signal = ports[2]         # 模拟总控收信号
    port_vision = ports[3]         # 模拟总控收视觉
    port_net = ports[4]            # 模拟总控收网络屏
    port_sig_screen = ports[5]     # 模拟总控收信号屏
    port_plc = find_free_ports(1)[0]  # 模拟 PLC

    print(f"  车辆 UDP(发):   127.0.0.1:{port_vehicle_send}")
    print(f"  车辆 UDP(收):   127.0.0.1:{port_vehicle_recv}")
    print(f"  信号 UDP:       127.0.0.1:{port_signal}")
    print(f"  视景 UDP:       127.0.0.1:{port_vision}")
    print(f"  网络屏 TCP:     127.0.0.1:{port_net}")
    print(f"  信号屏 TCP:     127.0.0.1:{port_sig_screen}")
    print(f"  PLC TCP:        127.0.0.1:{port_plc}")
    print()

    # ── 2. 启动接收端 ───────────────────────────────

    print("正在启动接收端...")
    recv_vehicle = UDPReceiver("车辆UDP", port_vehicle_send, 600)
    recv_signal = UDPReceiver("信号网关", port_signal)
    recv_vision = UDPReceiver("视景UDP", port_vision)
    recv_net = TCPServer("网络屏TCP", port_net)
    recv_sig_screen = TCPServer("信号屏TCP", port_sig_screen)
    plc_sim = PLCSimulator(port_plc)

    # ── 3. patch 常量，指向本地端口 ─────────────────

    from unittest.mock import patch

    patches = [
        # 车辆 UDP
        patch("src.network.udp_vehicle.VEHICLE_UDP_LOCAL_ADDR", "127.0.0.1"),
        patch("src.network.udp_vehicle.VEHICLE_UDP_REMOTE_ADDR", "127.0.0.1"),
        patch("src.network.udp_vehicle.VEHICLE_UDP_LOCAL_PORT", port_vehicle_recv),
        patch("src.network.udp_vehicle.VEHICLE_UDP_REMOTE_PORT", port_vehicle_send),
        patch("src.network.udp_vehicle.VEHICLE_UDP_CYCLE_MS", 200),
        # 信号网关
        patch("src.network.signal_gateway.SIGNAL_GATEWAY_ADDR", "127.0.0.1"),
        patch("src.network.signal_gateway.SIGNAL_GATEWAY_PORT", port_signal),
        patch("src.network.signal_gateway.SIGNAL_LOCAL_PORT", 0),
        patch("src.network.signal_gateway.SIGNAL_GATEWAY_CYCLE_MS", 200),
        # 视景
        patch("src.network.vision_udp.VISION_LOCAL_ADDR", "127.0.0.1"),
        patch("src.network.vision_udp.VISION_REMOTE_ADDR", "127.0.0.1"),
        patch("src.network.vision_udp.VISION_REMOTE_PORT", port_vision),
        patch("src.network.vision_udp.VISION_CYCLE_MS", 200),
        # 司机台
        patch("src.network.cab_display.CAB_DISPLAY_ADDR", "127.0.0.1"),
        patch("src.network.cab_display.CAB_NETWORK_SCREEN_PORT", port_net),
        patch("src.network.cab_display.CAB_SIGNAL_SCREEN_PORT", port_sig_screen),
        patch("src.network.cab_display.CAB_DISPLAY_CYCLE_MS", 200),
        # PLC
        patch("src.network.tcp_plc.PLC_SERVER_ADDR", "127.0.0.1"),
        patch("src.network.tcp_plc.PLC_PORT_1", port_plc),
        patch("src.network.tcp_plc.PLC_PORT_2", 0),
        patch("src.network.tcp_plc.PLC_PORT_3", 0),
        patch("src.network.tcp_plc.PLC_CYCLE_MS", 200),
    ]

    for p in patches:
        p.start()

    # ── 4. 启动 NetworkManager ─────────────────────

    from src.network import NetworkManager

    mock = MockDataSource()
    net = NetworkManager()

    # 设置车辆发送（回发时也发控制指令）
    net.set_vehicle_send_source(mock.vehicle_data)
    # 设置车辆接收回调
    received_commands = []

    def on_vehicle_recv(commands):
        received_commands.append(commands)

    net.set_vehicle_recv_callback(on_vehicle_recv)

    # 信号
    net.set_signal_send_source(mock.signal_data)

    # 视景
    net.set_vision_data_source(mock.vision_data)

    # 司机台
    net.set_cab_network_source(mock.cab_network_data)
    net.set_cab_signal_source(mock.cab_signal_data)

    # PLC
    received_plc = []

    def on_plc(data):
        received_plc.append(data)

    net.set_plc_recv_callback(on_plc)

    # ── 5. 启动 ─────────────────────────────────────

    print("正在启动 NetworkManager 通信线程...")
    net.start()
    time.sleep(0.5)  # 等待首次握手
    print()
    print("=" * 60)
    print("  [OK] 所有通信模块已启动，正在运行中...")
    print("  Press Ctrl+C 停止")
    print("=" * 60)
    print()

    # ── 6. 监控循环 ─────────────────────────────────

    try:
        while True:
            status = net.connection_status
            print(f"[{time.strftime('%H:%M:%S')}]  ── 状态报告 ──")
            for name, connected in status.items():
                pkt = 0
                if name == "vehicle_udp":
                    pkt = recv_vehicle.packet_count
                elif name == "signal_gateway":
                    pkt = recv_signal.packet_count
                elif name == "vision":
                    pkt = recv_vision.packet_count
                elif name == "cab_display":
                    pkt = recv_net.packet_count + recv_sig_screen.packet_count
                ok = "[OK]" if connected else "[NO]"
                print(f"    {ok} {name:15s}   收 {pkt:4d} 包")

            plc_info = f"手柄级位: {received_plc[-1]['handle_position']}级" if received_plc else "等待中..."
            print(f"    PLC 数据: {plc_info}")
            print()

            # 每 2 秒发一条控制指令给车辆 UDP 接收端，模拟总控平台下发指令
            # （打包成平台→车辆格式：每车 2 double: 指令, 百分比）
            cmd_data = struct.pack("<dd", 1.0, 60.0)  # 加速指令 60%
            cmd_data *= 20  # 20 车
            cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            cmd_sock.sendto(cmd_data, ("127.0.0.1", port_vehicle_recv))
            cmd_sock.close()

            time.sleep(2.0)

    except KeyboardInterrupt:
        print()
        print("正在停止...")
    finally:
        net.stop()
        for p in patches:
            p.stop()
        for r in [recv_vehicle, recv_signal, recv_vision]:
            r.stop()
        for r in [recv_net, recv_sig_screen]:
            r.stop()
        plc_sim.stop()
        print()
        print("=" * 60)
        print("  已停止。测试汇总：")
        print(f"    车辆 UDP 收: {recv_vehicle.packet_count} 包")
        print(f"    信号 UDP 收: {recv_signal.packet_count} 包")
        print(f"    视景 UDP 收: {recv_vision.packet_count} 包")
        print(f"    网络屏 TCP 收: {recv_net.packet_count} 包")
        print(f"    信号屏 TCP 收: {recv_sig_screen.packet_count} 包")
        print(f"    PLC 指令 收: {len(received_plc)} 条")
        print("=" * 60)


if __name__ == "__main__":
    main()
