"""司机台显示屏 TCP 通信模块

通过 TCP 向总控发送司机台显示数据，由总控转发给硬件司机台。
- 网络屏: 572 bytes, 端口 8888
- 信号屏: 66 bytes, 端口 9999
- 周期: 100ms

协议参考: 《轨交多系统平台接口协议汇总.md》司机驾驶模拟台网络屏/信号屏协议
"""

import socket
import threading
import logging
import time
from typing import Optional, Callable
from .constants import (
    CAB_DISPLAY_ADDR, CAB_NETWORK_SCREEN_PORT,
    CAB_SIGNAL_SCREEN_PORT, CAB_DISPLAY_CYCLE_MS,
)
from .codec import pack_network_screen, pack_signal_screen

logger = logging.getLogger(__name__)


class CabDisplayClient:
    """司机台显示屏客户端

    在独立线程中以 100ms 周期发送网络屏和信号屏数据。
    外部系统不可用时静默降级。
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 数据源回调
        self._network_data_source: Optional[Callable[[], dict]] = None
        self._signal_data_source: Optional[Callable[[], dict]] = None

        self.connected = False

    def set_network_data_source(self, source: Callable[[], dict]):
        """设置网络屏数据源"""
        self._network_data_source = source

    def set_signal_data_source(self, source: Callable[[], dict]):
        """设置信号屏数据源"""
        self._signal_data_source = source

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="CabDisplay")
        self._thread.start()
        logger.info("司机台显示TCP通信线程已启动")

    def stop(self):
        self._running = False
        self.connected = False

    def _send_tcp(self, addr: str, port: int, data: bytes):
        """发送 TCP 数据到指定地址和端口"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((addr, port))
            s.sendall(data)
            s.close()
        except Exception as e:
            logger.debug("TCP发送失败 %s:%d - %s", addr, port, e)

    def _run(self):
        import time as _time_mod

        while self._running:
            cycle_start = time.perf_counter()
            timestamp_ms = int(_time_mod.time() * 1000)

            try:
                has_network = False
                has_signal = False

                # --- 网络屏 (572 bytes → 总控:8888) ---
                if self._network_data_source:
                    data = self._network_data_source()
                    if data:
                        packet = pack_network_screen(
                            speed=data.get("speed", 0.0),
                            acceleration=data.get("acceleration", 0.0),
                            speed_limit=data.get("speed_limit", 0.0),
                            run_mode=data.get("run_mode", 0),
                            run_dir=data.get("run_dir", 0),
                            power_pull=data.get("power_pull", 0),
                            net_pressure=data.get("net_pressure", 0),
                            curr_station=data.get("curr_station", 0),
                            next_station=data.get("next_station", 0),
                            end_station=data.get("end_station", 0),
                            power_state=data.get("power_state", 0),
                            door_states=data.get("door_states"),
                            has_power=data.get("has_power", True),
                            timestamp_ms=timestamp_ms,
                        )
                        self._send_tcp(CAB_DISPLAY_ADDR, CAB_NETWORK_SCREEN_PORT, packet)
                        has_network = True

                # --- 信号屏 (66 bytes → 总控:9999) ---
                if self._signal_data_source:
                    data = self._signal_data_source()
                    if data:
                        packet = pack_signal_screen(
                            speed=data.get("speed", 0.0),
                            acceleration=data.get("acceleration", 0.0),
                            speed_limit=data.get("speed_limit", 0.0),
                            mode=data.get("mode", 5),
                            run_dir=data.get("run_dir", 0),
                            curr_station=data.get("curr_station", 0),
                            next_station=data.get("next_station", 0),
                            end_station=data.get("end_station", 0),
                            pull_switch=data.get("pull_switch", 0),
                            pull_state=data.get("pull_state", 0),
                            brake_state=data.get("brake_state", 0),
                            urgency_stop=data.get("urgency_stop", 0),
                            event_id=data.get("event_id", 0),
                            sig_state=data.get("sig_state", 0),
                            train_no=data.get("train_no", 1),
                            next_station_dist=data.get("next_station_dist", 0.0),
                            timestamp_ms=timestamp_ms,
                        )
                        self._send_tcp(CAB_DISPLAY_ADDR, CAB_SIGNAL_SCREEN_PORT, packet)
                        has_signal = True

                self.connected = has_network or has_signal

            except Exception as e:
                logger.debug("司机台显示发送异常: %s", e)

            elapsed = (time.perf_counter() - cycle_start) * 1000
            sleep_ms = max(0, CAB_DISPLAY_CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)
