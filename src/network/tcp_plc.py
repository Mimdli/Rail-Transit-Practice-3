"""司机台PLC协议模块

实现与司机驾驶模拟台PLC的TCP通信。
PLC作为TCP服务端，仿真系统作为客户端主动连接。
- 3端口: 8001(主控), 8002(备用), 8003(备用)
- PLC→上位机: 46字节, 100ms周期
- 上位机→PLC: 26字节, 按需发送

协议参考: 《轨交多系统平台接口协议汇总.md》司机驾驶模拟台PLC协议
"""

import socket
import threading
import logging
import time
from typing import Optional, Callable
from .constants import (
    PLC_SERVER_ADDR, PLC_PORT_1, PLC_PORT_2, PLC_PORT_3,
    PLC_CYCLE_MS, PLC_RECV_SIZE, PLC_SEND_SIZE,
)
from .codec import unpack_plc_data, pack_plc_output

logger = logging.getLogger(__name__)


class PLCClient:
    """司机台PLC客户端"""

    def __init__(self):
        self._sockets: list[Optional[socket.socket]] = [None, None, None]
        self._ports = [PLC_PORT_1, PLC_PORT_2, PLC_PORT_3]
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 回调
        self._recv_callback: Optional[Callable[[dict], None]] = None

        # 最近收到的PLC数据
        self._last_plc_data: Optional[dict] = None
        self.connected = False

        # 统计信息
        self.packets_sent = 0
        self.packets_received = 0
        self.last_send_time = 0.0
        self.last_recv_time = 0.0

        # 最近收发的原始报文（hex dump用）
        self.last_sent_packet: bytes = b''
        self.last_recv_packet: bytes = b''

    @property
    def last_plc_data(self) -> Optional[dict]:
        return self._last_plc_data

    def set_recv_callback(self, cb: Callable[[dict], None]):
        self._recv_callback = cb

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="PLCClient")
        self._thread.start()
        logger.info("司机台PLC客户端已启动")

    def stop(self):
        self._running = False
        for i in range(3):
            if self._sockets[i]:
                try:
                    self._sockets[i].close()
                except Exception:
                    pass
                self._sockets[i] = None
        self.connected = False

    def send_output(self, **kwargs):
        """发送上位机输出到PLC，kwargs 透传至 pack_plc_output"""
        data = pack_plc_output(**kwargs)
        for s in self._sockets:
            if s:
                try:
                    s.sendall(data)
                    self.packets_sent += 1
                    self.last_sent_packet = data
                    self.last_send_time = time.time()
                except Exception:
                    pass

    def _run(self):
        # 连接三个端口（port <= 0 的跳过）
        for i, port in enumerate(self._ports):
            if port <= 0:
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect((PLC_SERVER_ADDR, port))
                s.settimeout(0.01)
                self._sockets[i] = s
                logger.info("PLC端口 %d 连接成功", port)
            except Exception as e:
                logger.warning("PLC端口 %d 连接失败: %s", port, e)

        if not any(self._sockets):
            logger.warning("所有PLC端口均连接失败，PLC客户端降级运行")
            # 继续运行，但只做标记

        while self._running:
            cycle_start = time.perf_counter()

            self.connected = any(s is not None for s in self._sockets)

            for s in self._sockets:
                if s is None:
                    continue
                try:
                    data = s.recv(PLC_RECV_SIZE)
                    if len(data) >= PLC_RECV_SIZE:
                        parsed = unpack_plc_data(data)
                        if parsed:
                            self._last_plc_data = parsed
                            self.last_recv_packet = data
                            self.packets_received += 1
                            self.last_recv_time = time.time()
                            if self._recv_callback:
                                self._recv_callback(parsed)
                except socket.timeout:
                    pass
                except Exception as e:
                    logger.debug("PLC接收异常: %s", e)

            elapsed = (time.perf_counter() - cycle_start) * 1000
            sleep_ms = max(0, PLC_CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)
