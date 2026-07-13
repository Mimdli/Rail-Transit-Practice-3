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
            "vision": self._module_stats(self.vision, "视景系统", "UDP", 100, False, now),
            "cab_display": self._module_stats(self.cab_display, "司机台显示", "TCP", 100, True, now),
        }
        # cab_display 额外携带两个屏各自的报文
        cab = result["cab_display"]
        cab["last_network_packet_hex"] = self._hex_str(
            getattr(self.cab_display, "last_network_packet", b""))
        cab["last_signal_packet_hex"] = self._hex_str(
            getattr(self.cab_display, "last_signal_packet", b""))
        return result

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

    def set_vehicle_recv_callback(self, cb: Callable[[list[tuple[float, float, float]]], None]):
        """设置车辆接收回调"""
        self.vehicle_udp.set_recv_callback(cb)

    @property
    def vehicle_commands(self) -> list[tuple[float, float]]:
        return self.vehicle_udp.last_commands

    # ---- 信号网关 ----

    def set_signal_send_source(self, source: Callable[[], tuple[list, list]]):
        """设置信号发送数据源"""
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
        self.plc.send_output(atp_safe_out)

    @property
    def plc_data(self) -> Optional[dict]:
        return self.plc.last_plc_data

    # ---- 视景系统 ----

    def set_vision_data_source(self, source: Callable[[], dict]):
        """设置视景系统数据源"""
        self.vision.set_data_source(source)

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
