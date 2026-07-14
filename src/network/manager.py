"""网络通信管理器

统一协调所有子系统通信模块的生命周期：
- 车辆UDP (20ms)
- 信号系统网关 (250ms/100ms)
- 司机台PLC (100ms)
- 视景系统UDP (100ms)
- 司机台显示屏TCP (100ms)

所有模块在独立线程中运行，外部系统不可用时静默降级。
"""

import logging
from typing import Optional, Callable

from .udp_vehicle import VehicleUDPClient
from .signal_gateway import SignalGateway
from .tcp_plc import PLCClient
from .vision_udp import VisionUDPClient
from .cab_display import CabDisplayClient
from .constants import (
    VEHICLE_UDP_LOCAL_ADDR, VEHICLE_UDP_LOCAL_PORT,
    VEHICLE_UDP_REMOTE_ADDR, VEHICLE_UDP_REMOTE_PORT,
    VEHICLE_UDP_SEND_SIZE, VEHICLE_UDP_RECV_SIZE,
    SIGNAL_GATEWAY_ADDR, SIGNAL_GATEWAY_PORT, SIGNAL_LOCAL_PORT,
    PLC_SERVER_ADDR, PLC_PORT_1, PLC_PORT_2, PLC_PORT_3,
    PLC_SEND_SIZE, PLC_RECV_SIZE,
    VISION_LOCAL_ADDR, VISION_LOCAL_PORT, VISION_REMOTE_ADDR, VISION_REMOTE_PORT,
    CAB_NETWORK_SCREEN_ADDR, CAB_NETWORK_SCREEN_PORT,
    CAB_SIGNAL_SCREEN_ADDR, CAB_SIGNAL_SCREEN_PORT,
)

logger = logging.getLogger(__name__)


class NetworkManager:
    """多系统通信管理器"""

    def __init__(self):
        # 子系统
        self.vehicle_udp = VehicleUDPClient()
        self.signal_gateway = SignalGateway()
        self.plc = PLCClient()
        self.vision = VisionUDPClient()
        self.cab_display = CabDisplayClient()

        # 总开关
        self._running = False

        # 连接状态摘要
        self._connection_status: dict[str, bool] = {
            "vehicle_udp": False,
            "signal_gateway": False,
            "plc": False,
            "vision": False,
            "cab_display": False,
        }

    @property
    def connection_status(self) -> dict[str, bool]:
        """获取各子系统连接状态"""
        self._connection_status["vehicle_udp"] = self.vehicle_udp.connected
        self._connection_status["signal_gateway"] = self.signal_gateway.connected
        self._connection_status["plc"] = self.plc.connected
        self._connection_status["vision"] = self.vision.connected
        self._connection_status["cab_display"] = self.cab_display.connected
        return dict(self._connection_status)

    @property
    def network_stats(self) -> dict:
        """获取各子系统详细通信统计"""
        now = __import__("time").time()
        result = {
            "vehicle_udp": self._module_stats(self.vehicle_udp, "车辆UDP", "UDP", 20, True, now),
            "signal_gateway": self._module_stats(self.signal_gateway, "信号网关", "UDP", 250, True, now),
            "plc": self._module_stats(self.plc, "司机台PLC", "TCP", 100, True, now),
            "vision": self._module_stats(self.vision, "视景系统", "UDP", 100, True, now),
            "cab_display": self._module_stats(self.cab_display, "司机台显示", "TCP", 100, True, now),
        }
        # cab_display 额外携带两个屏各自的报文
        cab = result["cab_display"]
        cab["last_network_packet_hex"] = self._hex_str(
            getattr(self.cab_display, "last_network_packet", b""))
        cab["last_signal_packet_hex"] = self._hex_str(
            getattr(self.cab_display, "last_signal_packet", b""))
        result["vehicle_udp"]["endpoints"] = [self._endpoint_stats(
            self.vehicle_udp, "vehicle_udp", "车辆状态接口", "UDP",
            f"{VEHICLE_UDP_LOCAL_ADDR}:{VEHICLE_UDP_LOCAL_PORT}",
            f"{VEHICLE_UDP_REMOTE_ADDR}:{VEHICLE_UDP_REMOTE_PORT}",
            "双向", f"{VEHICLE_UDP_SEND_SIZE} / {VEHICLE_UDP_RECV_SIZE} B", now)]
        result["signal_gateway"]["endpoints"] = [self._endpoint_stats(
            self.signal_gateway, "signal_gateway", "信号系统网关", "UDP",
            f"0.0.0.0:{SIGNAL_LOCAL_PORT}",
            f"{SIGNAL_GATEWAY_ADDR}:{SIGNAL_GATEWAY_PORT}",
            "双向", "变长", now)]
        result["vision"]["endpoints"] = [self._endpoint_stats(
            self.vision, "vision", "视景系统", "UDP",
            f"{VISION_LOCAL_ADDR}:{VISION_LOCAL_PORT}",
            f"{VISION_REMOTE_ADDR}:{VISION_REMOTE_PORT}",
            "双向", "154 B（抓包）", now)]
        result["plc"]["endpoints"] = [
            self._plc_endpoint(index, port, now)
            for index, port in enumerate((PLC_PORT_1, PLC_PORT_2, PLC_PORT_3))
        ]
        result["cab_display"]["endpoints"] = [
            self._cab_endpoint("cab_network", "网络屏", CAB_NETWORK_SCREEN_ADDR,
                               CAB_NETWORK_SCREEN_PORT, "双向", "570 / 570 B", now),
            self._cab_endpoint("cab_signal", "信号屏", CAB_SIGNAL_SCREEN_ADDR,
                               CAB_SIGNAL_SCREEN_PORT, "发送", "68 B", now),
        ]
        return result

    @staticmethod
    def _age(now: float, timestamp: float):
        return round((now - timestamp) * 1000) if timestamp > 0 else None

    def _endpoint_stats(self, module, endpoint_id, name, protocol, local,
                        remote, direction, frame_size, now):
        return {
            "id": endpoint_id, "name": name, "protocol": protocol,
            "local": local, "remote": remote, "direction": direction,
            "frame_size": frame_size, "connected": module.connected,
            "packets_sent": getattr(module, "packets_sent", 0),
            "packets_received": getattr(module, "packets_received", 0),
            "last_send_ago": self._age(now, getattr(module, "last_send_time", 0.0)),
            "last_recv_ago": self._age(now, getattr(module, "last_recv_time", 0.0)),
            "last_sent_packet_hex": self._hex_str(getattr(module, "last_sent_packet", b"")),
            "last_recv_packet_hex": self._hex_str(getattr(module, "last_recv_packet", b"")),
        }

    def _plc_endpoint(self, index: int, port: int, now: float) -> dict:
        plc = self.plc
        return {
            "id": f"plc_{port}", "name": f"PLC {port}", "protocol": "TCP",
            "local": "系统临时端口", "remote": f"{PLC_SERVER_ADDR}:{port}",
            "direction": "双向", "frame_size": f"{PLC_SEND_SIZE} / {PLC_RECV_SIZE} B",
            "connected": plc._sockets[index] is not None,
            "packets_sent": plc.port_packets_sent[index],
            "packets_received": plc.port_packets_received[index],
            "last_send_ago": self._age(now, plc.port_last_send_time[index]),
            "last_recv_ago": self._age(now, plc.port_last_recv_time[index]),
            "last_sent_packet_hex": self._hex_str(plc.port_last_sent_packet[index]),
            "last_recv_packet_hex": self._hex_str(plc.port_last_recv_packet[index]),
        }

    def _cab_endpoint(self, endpoint_id, name, addr, port, direction,
                      frame_size, now) -> dict:
        cab = self.cab_display
        prefix = "network" if endpoint_id == "cab_network" else "signal"
        sent_packet = cab.last_network_packet if prefix == "network" else cab.last_signal_packet
        recv_packet = cab.last_network_recv_packet if prefix == "network" else b""
        return {
            "id": endpoint_id, "name": name, "protocol": "TCP",
            "local": "系统临时端口", "remote": f"{addr}:{port}",
            "direction": direction, "frame_size": frame_size,
            "connected": getattr(cab, f"{prefix}_connected"),
            "packets_sent": getattr(cab, f"{prefix}_packets_sent"),
            "packets_received": getattr(cab, f"{prefix}_packets_received"),
            "last_send_ago": self._age(now, getattr(cab, f"{prefix}_last_send_time")),
            "last_recv_ago": self._age(now, getattr(cab, f"{prefix}_last_recv_time")),
            "last_sent_packet_hex": self._hex_str(sent_packet),
            "last_recv_packet_hex": self._hex_str(recv_packet),
        }

    @staticmethod
    def _hex_str(data: bytes, max_len: int = 1024) -> str:
        """将 bytes 格式化为每行 8 字节的 hex dump 字符串"""
        if not data:
            return ""
        data = data[:max_len]
        rows = []
        for i in range(0, len(data), 8):
            chunk = data[i:i + 8]
            rows.append(" ".join(f"{b:02X}" for b in chunk))
        return "\n".join(rows)

    @staticmethod
    def _module_stats(module, name, protocol, cycle_ms, bidirectional, now):
        """构建单个模块的统计字典"""
        last_send = getattr(module, 'last_send_time', 0.0)
        last_recv = getattr(module, 'last_recv_time', 0.0)
        return {
            "name": name,
            "protocol": protocol,
            "cycle_ms": cycle_ms,
            "connected": module.connected,
            "bidirectional": bidirectional,
            "packets_sent": getattr(module, 'packets_sent', 0),
            "packets_received": getattr(module, 'packets_received', 0),
            "last_send_ago": round((now - last_send) * 1000) if last_send > 0 else None,
            "last_recv_ago": round((now - last_recv) * 1000) if last_recv > 0 else None,
            "last_sent_packet_hex": NetworkManager._hex_str(
                getattr(module, "last_sent_packet", b"")),
            "last_recv_packet_hex": NetworkManager._hex_str(
                getattr(module, "last_recv_packet", b"")),
        }

    @property
    def is_any_connected(self) -> bool:
        """是否有任一子系统已连接"""
        return any(self.connection_status.values())

    # ---- 车辆UDP ----

    def set_vehicle_send_source(self, source: Callable[[], list[tuple[float, float, float]]]):
        """设置车辆数据源回调"""
        self.vehicle_udp.set_send_source(source)

    def set_vehicle_recv_callback(self, cb: Callable[[list[tuple[float, float]]], None]):
        """设置车辆接收回调"""
        self.vehicle_udp.set_recv_callback(cb)

    @property
    def vehicle_commands(self) -> list[tuple[float, float]]:
        return self.vehicle_udp.last_commands

    # ---- 信号网关 ----

    def set_signal_send_source(self, source: Callable[[], tuple]):
        """设置信号发送数据源，可附带列车牵引制动命令。"""
        self.signal_gateway.set_send_source(source)

    def set_signal_recv_callback(self, cb: Callable[[bytes], None]):
        """设置信号接收回调"""
        self.signal_gateway.set_recv_callback(cb)

    # ---- PLC ----

    def set_plc_recv_callback(self, cb: Callable[[dict], None]):
        """设置PLC数据接收回调"""
        self.plc.set_recv_callback(cb)

    def send_plc_output(self, atp_safe_out: int = 0):
        """发送输出到PLC"""
        self.plc.send_output(output_bits=atp_safe_out)

    @property
    def plc_data(self) -> Optional[dict]:
        return self.plc.last_plc_data

    # ---- 视景系统 ----

    def set_vision_data_source(self, source: Callable[[], dict]):
        """设置视景系统发送数据源。"""
        self.vision.set_data_source(source)

    def set_vision_recv_callback(
        self, callback: Callable[[bytes, tuple[str, int]], None]
    ):
        """设置视景系统原始 UDP 报文回调。"""
        self.vision.set_recv_callback(callback)

    # ---- 司机台显示屏 ----

    def set_cab_network_source(self, source: Callable[[], dict]):
        """设置网络屏数据源"""
        self.cab_display.set_network_data_source(source)

    def set_cab_signal_source(self, source: Callable[[], dict]):
        """设置信号屏数据源"""
        self.cab_display.set_signal_data_source(source)

    # ---- 生命周期 ----

    def start(self):
        """启动所有通信模块"""
        if self._running:
            return
        self._running = True

        self.vehicle_udp.start()
        self.signal_gateway.start()
        self.plc.start()
        self.vision.start()
        self.cab_display.start()

        logger.info("NetworkManager: 所有通信模块已启动")

    def stop(self):
        """停止所有通信模块"""
        self._running = False
        self.vehicle_udp.stop()
        self.signal_gateway.stop()
        self.plc.stop()
        self.vision.stop()
        self.cab_display.stop()
        logger.info("NetworkManager: 所有通信模块已停止")
