"""视景系统 UDP 通信模块

向总控系统发送 TCMS2VIEW 报文，由总控转发给视景系统。
- 周期: 100ms
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
    """视景系统 UDP 客户端

    在独立线程中以 100ms 周期发送 TCMS2VIEW 报文。
    外部系统不可用时静默降级。
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._live_counter = 0

        # 数据源回调
        self._data_source: Optional[Callable[[], dict]] = None

        self.connected = False
        self._last_send_time = 0.0

        # 统计信息 (纯发送，无接收)
        self.packets_sent = 0
        self.last_send_time = 0.0

        # 最近发送的原始报文（hex dump用）
        self.last_sent_packet: bytes = b''

    def set_data_source(self, source: Callable[[], dict]):
        """设置数据源回调，返回包含 TCMS2VIEW 各字段的 dict"""
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
        self._sock.settimeout(0.01)
        try:
            self._sock.bind((VISION_LOCAL_ADDR, VISION_LOCAL_PORT))
        except OSError:
            pass

        remote = (VISION_REMOTE_ADDR, VISION_REMOTE_PORT)

        while self._running:
            cycle_start = time.perf_counter()

            if self._data_source:
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
                        self._last_send_time = time.time()
                        if self._live_counter % 100 == 0:
                            logger.debug("视景UDP已发送 %d 报文", self._live_counter)
                except Exception as e:
                    logger.debug("视景UDP发送失败: %s", e)

            elapsed = (time.perf_counter() - cycle_start) * 1000
            sleep_ms = max(0, VISION_CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)
