"""视景系统 UDP 双向通信模块。

周期发送 TCMS2VIEW 状态，同时保留接收的完整原始数据供日志记录。
- 协议: UDP, strTCMS2VIEW 结构体

协议参考: 《轨交多系统平台接口协议汇总.md》§3 视景系统
"""

import socket
import threading
import logging
import time
from typing import Optional, Callable
from .constants import (
    VISION_LOCAL_ADDR, VISION_LOCAL_PORT, VISION_REMOTE_ADDR,
    VISION_REMOTE_PORT, VISION_CYCLE_MS,
)
from .codec import pack_vision_tcms2view

logger = logging.getLogger(__name__)


class VisionUDPClient:
    """视景系统 UDP 双向端点。

    在独立线程中周期发送仿真状态，并监听对侧返回的完整 UDP 报文。
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._recv_callback: Optional[
            Callable[[bytes, tuple[str, int]], None]
        ] = None
        self._data_source: Optional[Callable[[], dict]] = None
        self._live_counter = 0

        self.connected = False
        self.packets_sent = 0
        self.last_send_time = 0.0
        self.last_sent_packet: bytes = b''
        self.packets_received = 0
        self.last_recv_time = 0.0
        self.last_recv_packet: bytes = b''
        self.last_recv_addr: Optional[tuple[str, int]] = None

    def set_recv_callback(self, callback: Callable[[bytes, tuple[str, int]], None]):
        """设置报文回调，参数为完整 UDP 载荷和发送方地址。"""
        self._recv_callback = callback

    def set_data_source(self, source: Callable[[], dict]):
        """设置 TCMS2VIEW 实时数据源。"""
        self._data_source = source

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="VisionUDP")
        self._thread.start()
        logger.info("视景系统UDP通信线程已启动")

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
        self._sock.settimeout(max(VISION_CYCLE_MS / 1000, 0.1))
        try:
            self._sock.bind((VISION_LOCAL_ADDR, VISION_LOCAL_PORT))
        except OSError as exc:
            logger.error(
                "视景UDP监听失败 %s:%d: %s",
                VISION_LOCAL_ADDR, VISION_LOCAL_PORT, exc,
            )
            self._running = False
            return

        logger.info("视景UDP正在监听 %s:%d", VISION_LOCAL_ADDR, VISION_LOCAL_PORT)
        remote = (VISION_REMOTE_ADDR, VISION_REMOTE_PORT)
        next_send_time = 0.0

        while self._running:
            now = time.monotonic()
            if self._data_source and now >= next_send_time:
                next_send_time = now + VISION_CYCLE_MS / 1000
                try:
                    data = self._data_source()
                    if data:
                        self._live_counter += 1
                        packet = pack_vision_tcms2view(
                            live_counter=self._live_counter,
                            signal_states=data.get("signal_states", []),
                            switch_states=data.get("switch_states", []),
                            speed_mms=data.get("speed_mms", 0),
                            accel_pct=data.get("accel_pct", 0),
                            run_state=data.get("run_state", 0x13),
                            position_mm=data.get("position_mm", 0),
                            edge_id=data.get("edge_id", 0),
                            direction=data.get("direction", 1),
                            other_trains=data.get("other_trains"),
                        )
                        self._sock.sendto(packet, remote)
                        self.last_sent_packet = packet
                        self.packets_sent += 1
                        self.last_send_time = time.time()
                except Exception as exc:
                    logger.debug("视景UDP发送失败: %s", exc)

            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                stale_after = max(1.0, VISION_CYCLE_MS * 5 / 1000)
                if self.last_recv_time and time.time() - self.last_recv_time > stale_after:
                    self.connected = False
                continue
            except OSError as exc:
                if self._running:
                    logger.warning("视景UDP接收失败: %s", exc)
                break

            self.last_recv_packet = data
            self.last_recv_addr = addr
            self.packets_received += 1
            self.last_recv_time = time.time()
            self.connected = True

            if self._recv_callback:
                try:
                    self._recv_callback(data, addr)
                except Exception as exc:
                    logger.exception("记录视景UDP报文失败: %s", exc)
