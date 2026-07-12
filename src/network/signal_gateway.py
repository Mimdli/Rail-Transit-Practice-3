"""信号系统网关模块

通过UDP与总控数据库节点通信，实现信号系统与外部系统的数据交换。
- 周期发送：信号机状态、道岔状态 (250ms)
- 周期接收：列车信息、驾驶台开关量 (100ms)

协议参考:《轨交多系统平台接口协议汇总.md》§3.3
"""

import socket
import threading
import logging
import time
from typing import Optional, Callable
from .constants import (
    SIGNAL_GATEWAY_ADDR, SIGNAL_GATEWAY_PORT,
    SIGNAL_LOCAL_PORT, SIGNAL_GATEWAY_CYCLE_MS,
    SIGNAL_GATEWAY_RECV_CYCLE_MS,
)
from .codec import pack_signal_switch_signal, _make_signal_header

logger = logging.getLogger(__name__)


class SignalGateway:
    """信号系统网关

    在独立线程中运行，周期收发信号系统数据。
    外部系统不可用时不影响本地仿真。
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 数据源/回调
        self._send_source: Optional[Callable[[], tuple[list, list]]] = None
        self._recv_callback: Optional[Callable[[bytes], None]] = None

        self.connected = False
        self._last_recv_time = 0.0

        # 统计信息
        self.packets_sent = 0
        self.packets_received = 0
        self.last_send_time = 0.0
        self.last_recv_time = 0.0

    def set_send_source(self, source: Callable[[], tuple[list, list]]):
        """设置发送数据源，返回 (switches, signals)"""
        self._send_source = source

    def set_recv_callback(self, cb: Callable[[bytes], None]):
        """设置接收回调"""
        self._recv_callback = cb

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="SignalGateway")
        self._thread.start()
        logger.info("信号网关通信线程已启动")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self.connected = False

    def _run(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.02)
        try:
            self._sock.bind(("0.0.0.0", SIGNAL_LOCAL_PORT))
        except OSError:
            self._sock.bind(("0.0.0.0", 0))

        remote = (SIGNAL_GATEWAY_ADDR, SIGNAL_GATEWAY_PORT)

        # 使用最小周期作为循环周期，发送通过计数器节流
        loop_ms = min(SIGNAL_GATEWAY_CYCLE_MS, SIGNAL_GATEWAY_RECV_CYCLE_MS)
        send_counter = 0
        send_interval = max(1, SIGNAL_GATEWAY_CYCLE_MS // loop_ms)

        while self._running:
            cycle_start = time.perf_counter()
            send_counter += 1

            # 发送道岔/信号机状态 (每250ms)
            if send_counter >= send_interval:
                send_counter = 0
                if self._send_source:
                    try:
                        switches, signals = self._send_source()
                        data = pack_signal_switch_signal(switches, signals)
                        self._sock.sendto(data, remote)
                        self.packets_sent += 1
                        self.last_send_time = time.time()
                    except Exception as e:
                        logger.debug("信号网关发送失败: %s", e)

            # 接收 (每loop_ms)
            try:
                data, addr = self._sock.recvfrom(4096)
                if self._recv_callback:
                    self._recv_callback(data)
                self._last_recv_time = time.time()
                self.packets_received += 1
                self.connected = True
            except socket.timeout:
                pass
            except Exception as e:
                logger.debug("信号网关接收失败: %s", e)

            # 接收超时检测 (5s无数据则标记断开)
            if self.connected and time.time() - self._last_recv_time > 5:
                self.connected = False

            elapsed = (time.perf_counter() - cycle_start) * 1000
            sleep_ms = max(0, loop_ms - elapsed)
            time.sleep(sleep_ms / 1000)
