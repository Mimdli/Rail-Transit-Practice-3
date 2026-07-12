"""车辆UDP通信模块

实现车辆动力学模型与外部仿真平台之间的实时UDP通信。
- 周期: 20ms
- 模型→平台: 加速度/速度/累计里程 (20列车)
- 平台→模型: 加减速指令/百分比 (20列车)

协议参考: 《轨交多系统平台接口协议汇总.md》§2
"""

import time
import socket
import threading
import logging
from typing import Optional, Callable
from .constants import (
    VEHICLE_UDP_LOCAL_ADDR, VEHICLE_UDP_LOCAL_PORT,
    VEHICLE_UDP_REMOTE_ADDR, VEHICLE_UDP_REMOTE_PORT,
    VEHICLE_UDP_CYCLE_MS, VEHICLE_UDP_SEND_SIZE, VEHICLE_UDP_RECV_SIZE,
)
from .codec import pack_vehicle_udp, unpack_vehicle_udp

logger = logging.getLogger(__name__)


class VehicleUDPClient:
    """车辆UDP通信客户端

    在独立线程中以20ms周期收发数据。
    当外部平台不可用时静默降级，不影响本地仿真运行。
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 数据源/回调
        self._send_data_source: Optional[Callable[[], list[tuple[float, float, float]]]] = None
        self._recv_callback: Optional[Callable[[list[tuple[float, float, float]]], None]] = None

        # 最近接收的平台指令
        self._last_commands: list[tuple[float, float]] = []

        # 连接状态
        self.connected = False
        self._last_recv_time = 0.0

    @property
    def last_commands(self) -> list[tuple[float, float]]:
        """最近一次收到的平台指令列表"""
        return self._last_commands

    def set_send_source(self, source: Callable[[], list[tuple[float, float, float]]]):
        """设置发送数据源回调"""
        self._send_data_source = source

    def set_recv_callback(self, cb: Callable[[list[tuple[float, float, float]]], None]):
        """设置接收回调"""
        self._recv_callback = cb

    def start(self):
        """启动UDP通信线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="VehicleUDP")
        self._thread.start()
        logger.info("车辆UDP通信线程已启动")

    def stop(self):
        """停止UDP通信"""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self.connected = False
        logger.info("车辆UDP通信已停止")

    def _run(self):
        """通信主循环"""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.01)  # 10ms超时，可被stop快速中断
        try:
            self._sock.bind((VEHICLE_UDP_LOCAL_ADDR, VEHICLE_UDP_LOCAL_PORT))
        except OSError:
            logger.warning("车辆UDP端口绑定失败，使用随机端口")
            self._sock.bind(("0.0.0.0", 0))

        remote = (VEHICLE_UDP_REMOTE_ADDR, VEHICLE_UDP_REMOTE_PORT)
        import time

        while self._running:
            cycle_start = time.perf_counter()

            # --- 发送 ---
            if self._send_data_source:
                try:
                    trains = self._send_data_source()
                    data = pack_vehicle_udp(trains)
                    self._sock.sendto(data, remote)
                except Exception as e:
                    logger.debug("车辆UDP发送失败: %s", e)

            # --- 接收 ---
            try:
                data, addr = self._sock.recvfrom(VEHICLE_UDP_RECV_SIZE + 64)
                commands = unpack_vehicle_udp(data)
                self._last_commands = commands
                if self._recv_callback:
                    self._recv_callback(commands)
                self._last_recv_time = time.time()
                self.connected = True
            except socket.timeout:
                pass
            except Exception as e:
                logger.debug("车辆UDP接收失败: %s", e)

            # 接收超时检测 (5s无数据则标记断开)
            if self.connected and time.time() - self._last_recv_time > 5:
                self.connected = False

            # 精确控制周期
            elapsed = (time.perf_counter() - cycle_start) * 1000
            sleep_ms = max(0, VEHICLE_UDP_CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)
