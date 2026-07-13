"""网络通信常量定义

IP/端口从 config.network_config 统一读取，不再散落定义。
切换 本地仿真/实机联调 模式请修改 config.network_config.MODE。
"""

from typing import Final
from config.network_config import VEHICLE, SIGNAL, PLC, VISION, CAB_DISPLAY
from config.network_config import SIGNAL_HEADER

# ============================================================
# 车辆 UDP 通信（协议 §2）
# ============================================================
VEHICLE_UDP_LOCAL_ADDR: Final[str] = VEHICLE["local_addr"]
VEHICLE_UDP_LOCAL_PORT: Final[int] = VEHICLE["local_port"]
VEHICLE_UDP_REMOTE_ADDR: Final[str] = VEHICLE["remote_addr"]
VEHICLE_UDP_REMOTE_PORT: Final[int] = VEHICLE["remote_port"]
VEHICLE_UDP_CYCLE_MS: Final[int] = VEHICLE["cycle_ms"]

VEHICLE_UDP_TRAIN_COUNT: Final[int] = VEHICLE["train_count"]
VEHICLE_UDP_FIELDS_PER_TRAIN: Final[int] = 3  # 加速度, 速度, 累计里程
VEHICLE_UDP_SEND_SIZE: Final[int] = VEHICLE_UDP_TRAIN_COUNT * VEHICLE_UDP_FIELDS_PER_TRAIN * 8
VEHICLE_UDP_RECV_SIZE: Final[int] = VEHICLE_UDP_TRAIN_COUNT * 2 * 8

# 指令编码
CMD_COAST: Final[int] = 0   # 惰行
CMD_ACCEL: Final[int] = 1   # 加速
CMD_BRAKE: Final[int] = 2   # 减速

# ============================================================
# 信号系统 UDP 网关（协议 §3.3）
# ============================================================
SIGNAL_GATEWAY_ADDR: Final[str] = SIGNAL["remote_addr"]
SIGNAL_GATEWAY_PORT: Final[int] = SIGNAL["remote_port"]
SIGNAL_LOCAL_PORT: Final[int] = SIGNAL["local_port"]
SIGNAL_GATEWAY_CYCLE_MS: Final[int] = SIGNAL["cycle_ms"]
SIGNAL_GATEWAY_RECV_CYCLE_MS: Final[int] = SIGNAL["recv_cycle_ms"]

# 报文头源/目的标识（4字节）
SIG_SRC_TO_SIGNAL: Final[bytes] = SIGNAL_HEADER["src_id_to_signal"]       # 总控→信号 源
SIG_DST_TO_SIGNAL: Final[bytes] = SIGNAL_HEADER["dst_id_to_signal"]       # 总控→信号 目的
SIG_SRC_FROM_SIGNAL: Final[bytes] = SIGNAL_HEADER["src_id_from_signal"]   # 信号→总控 源
SIG_DST_FROM_SIGNAL: Final[bytes] = SIGNAL_HEADER["dst_id_from_signal"]   # 信号→总控 目的

# ============================================================
# 司机台 PLC 协议（协议 司机驾驶模拟台PLC协议 §2-6）
# ============================================================
PLC_SERVER_ADDR: Final[str] = PLC["server_addr"]
PLC_PORT_1: Final[int] = PLC["ports"][0]
PLC_PORT_2: Final[int] = PLC["ports"][1]
PLC_PORT_3: Final[int] = PLC["ports"][2]
PLC_CYCLE_MS: Final[int] = PLC["cycle_ms"]

PLC_RECV_SIZE: Final[int] = PLC["recv_size"]   # 46 bytes
PLC_SEND_SIZE: Final[int] = PLC["send_size"]   # 26 bytes

# ============================================================
# ATP DMI 通信（协议 ATP通信协议规范）
# ============================================================
ATP_DMI_CYCLE_MS: Final[int] = 160  # 160ms 周期

# ============================================================
# API 通信（协议 §3）
# ============================================================
API_CYCLE_MS: Final[int] = 500

# ============================================================
# 视景系统 UDP（协议 §3.2）
# ============================================================
VISION_LOCAL_ADDR: Final[str] = VISION["local_addr"]
VISION_LOCAL_PORT: Final[int] = VISION["local_port"]
VISION_REMOTE_ADDR: Final[str] = VISION["remote_addr"]
VISION_REMOTE_PORT: Final[int] = VISION["remote_port"]
VISION_CYCLE_MS: Final[int] = VISION["cycle_ms"]

# ============================================================
# 司机台显示 TCP（协议 § 网络屏 / 信号屏）
# ============================================================
CAB_NETWORK_SCREEN_ADDR: Final[str] = CAB_DISPLAY["network_screen_addr"]
CAB_NETWORK_SCREEN_PORT: Final[int] = CAB_DISPLAY["network_screen_port"]
CAB_SIGNAL_SCREEN_ADDR: Final[str] = CAB_DISPLAY["signal_screen_addr"]
CAB_SIGNAL_SCREEN_PORT: Final[int] = CAB_DISPLAY["signal_screen_port"]
CAB_DISPLAY_CYCLE_MS: Final[int] = CAB_DISPLAY["cycle_ms"]
