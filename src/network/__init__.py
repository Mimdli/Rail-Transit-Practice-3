"""轨道交通模拟系统 — 多系统接口通信模块

提供与外部信号系统、车辆平台、司机台PLC等子系统的网络通信能力。
所有通信在独立线程中运行，不阻塞主UI线程。
"""

from .manager import NetworkManager
from .constants import (
    # 车辆UDP
    VEHICLE_UDP_LOCAL_ADDR, VEHICLE_UDP_REMOTE_ADDR,
    VEHICLE_UDP_LOCAL_PORT, VEHICLE_UDP_REMOTE_PORT,
    # 信号网关UDP
    SIGNAL_GATEWAY_ADDR, SIGNAL_GATEWAY_PORT,
    SIGNAL_LOCAL_PORT,
    # 司机台PLC
    PLC_SERVER_ADDR, PLC_PORT_1, PLC_PORT_2, PLC_PORT_3,
)

__all__ = [
    "NetworkManager",
    "VEHICLE_UDP_LOCAL_ADDR", "VEHICLE_UDP_REMOTE_ADDR",
    "VEHICLE_UDP_LOCAL_PORT", "VEHICLE_UDP_REMOTE_PORT",
    "SIGNAL_GATEWAY_ADDR", "SIGNAL_GATEWAY_PORT",
    "SIGNAL_LOCAL_PORT",
    "PLC_SERVER_ADDR", "PLC_PORT_1", "PLC_PORT_2", "PLC_PORT_3",
]
