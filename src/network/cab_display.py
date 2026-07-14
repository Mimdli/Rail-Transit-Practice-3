"""司机台显示屏 TCP 通信模块

通过 TCP 向总控发送司机台显示数据，由总控转发给硬件司机台。
- 网络屏: 570 bytes, 端口 8888 (双向通信)
- 信号屏: 实际载荷 68 bytes, 端口 9999
- 周期: 100ms

协议参考: 《轨交多系统平台接口协议汇总.md》司机驾驶模拟台网络屏/信号屏协议
"""

import socket
import threading
import logging
import time
import struct
from typing import Optional, Callable
from .constants import (
    CAB_NETWORK_SCREEN_ADDR, CAB_NETWORK_SCREEN_PORT,
    CAB_SIGNAL_SCREEN_ADDR, CAB_SIGNAL_SCREEN_PORT, CAB_DISPLAY_CYCLE_MS,
)
from .codec import pack_network_screen, pack_signal_screen

logger = logging.getLogger(__name__)

# 网络屏接收帧同步头
SYNC_HEADER = b'\x55\xAA\x55\xAA'


class CabDisplayClient:
    """司机台显示屏客户端

    在独立线程中以 100ms 周期发送网络屏和信号屏数据。
    网络屏支持接收返回数据（TCP 持久连接）。
    外部系统不可用时静默降级。
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 数据源回调
        self._network_data_source: Optional[Callable[[], dict]] = None
        self._signal_data_source: Optional[Callable[[], dict]] = None

        self.connected = False

        # 统计信息
        self.packets_sent = 0
        self.packets_received = 0
        self.last_send_time = 0.0
        self.last_recv_time = 0.0

        # 最近报文
        self.last_network_packet: bytes = b''
        self.last_signal_packet: bytes = b''
        self.last_sent_packet: bytes = b''
        self.last_recv_packet: bytes = b''

        # 两块屏独立统计，避免 Web 端把网络屏和信号屏误显示为同一链路。
        self.network_connected = False
        self.signal_connected = False
        self.network_packets_sent = 0
        self.network_packets_received = 0
        self.signal_packets_sent = 0
        self.signal_packets_received = 0
        self.network_last_send_time = 0.0
        self.network_last_recv_time = 0.0
        self.signal_last_send_time = 0.0
        self.signal_last_recv_time = 0.0
        self.last_network_recv_packet: bytes = b''

        # TCP 持久连接（网络屏支持双向）
        self._net_sock: Optional[socket.socket] = None
        self._sig_sock: Optional[socket.socket] = None

        # 接收缓冲区
        self._recv_buf = bytearray()

        # 接收数据（解析后的值）
        self.received_data: dict = {}

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
        self.network_connected = False
        self.signal_connected = False
        self._close_sockets()

    def _close_sockets(self):
        """关闭所有持久连接"""
        for s in [self._net_sock, self._sig_sock]:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self._net_sock = None
        self._sig_sock = None

    def _ensure_socket(self, addr: str, port: int, sock_ref: list) -> Optional[socket.socket]:
        """确保持久 TCP 连接存在，断开时自动重连

        Args:
            addr: 目标地址
            port: 目标端口
            sock_ref: [sock] 列表引用，用于更新外部变量

        Returns:
            socket 对象，或 None 连接失败
        """
        s = sock_ref[0] if len(sock_ref) > 0 else None
        if s is not None:
            try:
                # 快速检查连接是否存活
                s.settimeout(0.001)
                s.send(b'', socket.MSG_OOB)
            except (OSError, AttributeError):
                pass
            try:
                s.settimeout(2.0)
                return s
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        # 重建连接
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((addr, port))
            sock_ref.clear()
            sock_ref.append(s)
            logger.info("TCP 连接已建立 %s:%d", addr, port)
            return s
        except Exception as e:
            logger.debug("TCP 连接失败 %s:%d - %s", addr, port, e)
            return None

    def _send_and_recv(self, s: socket.socket, data: bytes, recv: bool = False) -> bytes:
        """通过持久连接发送数据，可选接收响应

        Args:
            s: socket 对象
            data: 待发送数据
            recv: 是否尝试接收响应

        Returns:
            接收到的数据（空 bytes 表示无响应）
        """
        try:
            s.sendall(data)
            if not recv:
                return b''
            # 尝试接收响应
            s.settimeout(0.5)
            resp = b''
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                except socket.timeout:
                    break
            return resp
        except Exception as e:
            logger.debug("TCP 通信异常: %s", e)
            # 标记连接已断开
            raise

    def _find_frame(self, buf: bytearray) -> Optional[bytes]:
        """从缓冲区中查找并提取一个完整帧

        帧格式: 55 AA 55 AA + total_len(2B) + data_len(2B) + payload

        Args:
            buf: 接收缓冲区

        Returns:
            完整的数据帧，或 None 数据不足
        """
        # 查找同步头
        idx = buf.find(SYNC_HEADER)
        if idx < 0:
            # 丢弃无用的前缀数据（最多保留4字节用于部分匹配）
            if len(buf) > 4:
                buf.clear()
            return None

        if idx > 0:
            # 丢弃同步头前的无用数据
            del buf[:idx]

        # 需要至少 8 字节才能读取长度
        if len(buf) < 8:
            return None

        total_len = struct.unpack_from("<H", buf, 4)[0]
        if total_len < 8 or total_len > 4096:
            # 非法长度，丢弃
            del buf[:2]
            return None

        if len(buf) < total_len:
            return None

        frame = bytes(buf[:total_len])
        del buf[:total_len]
        return frame

    def _parse_network_screen_response(self, frame: bytes) -> dict:
        """解析网络屏返回数据帧

        目前仅提取帧头信息，具体数据字段按需扩展。
        """
        result = {}
        if len(frame) < 8:
            return result
        try:
            total_len = struct.unpack_from("<H", frame, 4)[0]
            data_len = struct.unpack_from("<H", frame, 6)[0]
            result["frame_total_len"] = total_len
            result["frame_data_len"] = data_len
            result["frame_raw_hex"] = frame.hex()[:128]  # 前128字符

            # 如果有数据载荷，尝试提取
            if len(frame) >= 24:
                result["timestamp"] = struct.unpack_from("<Q", frame, 8)[0]
        except Exception:
            pass
        return result

    def _run(self):
        import time as _time_mod

        while self._running:
            cycle_start = time.perf_counter()
            timestamp_ms = int(_time_mod.time() * 1000)

            try:
                has_network = False
                has_signal = False

                # --- 网络屏 (570 bytes → 192.168.100.121:8888) ---
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
                            train_no=data.get("train_no", 1),
                            timestamp_ms=timestamp_ms,
                        )
                        # 复用既有连接，避免每100ms重新占用设备唯一客户端连接。
                        sock_ref = [self._net_sock] if self._net_sock else []
                        s = self._ensure_socket(CAB_NETWORK_SCREEN_ADDR, CAB_NETWORK_SCREEN_PORT, sock_ref)
                        if s:
                            try:
                                resp = self._send_and_recv(s, packet, recv=True)
                                if resp:
                                    self._recv_buf.extend(resp)
                                    # 解析缓冲区中的帧
                                    while True:
                                        frame = self._find_frame(self._recv_buf)
                                        if frame is None:
                                            break
                                        self.received_data = self._parse_network_screen_response(frame)
                                        self.packets_received += 1
                                        self.last_recv_time = time.time()
                                        self.last_recv_packet = frame
                                        self.network_packets_received += 1
                                        self.network_last_recv_time = self.last_recv_time
                                        self.last_network_recv_packet = frame
                                has_network = True
                                self.network_connected = True
                                self.last_network_packet = packet
                                self.last_sent_packet = packet
                                self.packets_sent += 1
                                self.last_send_time = time.time()
                                self.network_packets_sent += 1
                                self.network_last_send_time = self.last_send_time
                                if sock_ref:
                                    self._net_sock = sock_ref[0]
                            except Exception:
                                if sock_ref:
                                    try:
                                        sock_ref[0].close()
                                    except Exception:
                                        pass
                                self._net_sock = None
                                self.network_connected = False
                        else:
                            self.network_connected = False

                # --- 信号屏 (字段表闭合为68 bytes → 192.168.100.122:9999) ---
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
                        sock_ref2 = [self._sig_sock] if self._sig_sock else []
                        s2 = self._ensure_socket(CAB_SIGNAL_SCREEN_ADDR, CAB_SIGNAL_SCREEN_PORT, sock_ref2)
                        if s2:
                            try:
                                self._send_and_recv(s2, packet, recv=False)
                                has_signal = True
                                self.signal_connected = True
                                self.last_signal_packet = packet
                                self.last_sent_packet = packet
                                self.packets_sent += 1
                                self.last_send_time = time.time()
                                self.signal_packets_sent += 1
                                self.signal_last_send_time = self.last_send_time
                                if sock_ref2:
                                    self._sig_sock = sock_ref2[0]
                            except Exception:
                                if sock_ref2:
                                    try:
                                        sock_ref2[0].close()
                                    except Exception:
                                        pass
                                self._sig_sock = None
                                self.signal_connected = False
                        else:
                            self.signal_connected = False

                self.connected = has_network or has_signal

            except Exception as e:
                logger.debug("司机台显示发送异常: %s", e)

            elapsed = (time.perf_counter() - cycle_start) * 1000
            sleep_ms = max(0, CAB_DISPLAY_CYCLE_MS - elapsed)
            time.sleep(sleep_ms / 1000)
