"""网络通信常量定义

数据来源：《轨交多系统平台接口协议汇总.md》
"""

from typing import Final

# ============================================================
# 车辆 UDP 通信（协议 §2）
# ============================================================
VEHICLE_UDP_LOCAL_ADDR: Final[str] = "192.168.200.110"   # 模型侧IP
VEHICLE_UDP_LOCAL_PORT: Final[int] = 23001               # 模型侧端口
VEHICLE_UDP_REMOTE_ADDR: Final[str] = "192.168.200.102"  # 平台侧IP
VEHICLE_UDP_REMOTE_PORT: Final[int] = 23002              # 平台侧端口
VEHICLE_UDP_CYCLE_MS: Final[int] = 20                    # 20ms 周期

# 车辆UDP报文：模型→平台 (20列車 × 3 double = 60 double = 480 bytes)
VEHICLE_UDP_TRAIN_COUNT: Final[int] = 20
VEHICLE_UDP_FIELDS_PER_TRAIN: Final[int] = 3  # 加速度, 速度, 累计里程
VEHICLE_UDP_SEND_SIZE: Final[int] = VEHICLE_UDP_TRAIN_COUNT * VEHICLE_UDP_FIELDS_PER_TRAIN * 8

# 车辆UDP报文：平台→模型 (20列車 × 2 double = 40 double = 320 bytes)
VEHICLE_UDP_RECV_SIZE: Final[int] = VEHICLE_UDP_TRAIN_COUNT * 2 * 8

# 指令编码
CMD_COAST: Final[int] = 0   # 惰行
CMD_ACCEL: Final[int] = 1   # 加速
CMD_BRAKE: Final[int] = 2   # 减速

# ============================================================
# 信号系统 UDP 网关（协议 §3.3）
# ============================================================
SIGNAL_GATEWAY_ADDR: Final[str] = "192.168.200.102"  # 总控数据库节点
SIGNAL_GATEWAY_PORT: Final[int] = 10000
SIGNAL_LOCAL_PORT: Final[int] = 24002
SIGNAL_GATEWAY_CYCLE_MS: Final[int] = 250            # 信号→总控 250ms
SIGNAL_GATEWAY_RECV_CYCLE_MS: Final[int] = 100       # 总控→信号 100ms

# 报文头固定值
SIG_HEADER_0: Final[int] = 0xff
SIG_HEADER_1_SEND: Final[int] = 0xf0   # 信号→总控
SIG_HEADER_1_RECV: Final[int] = 0xf0   # 总控→信号

# ============================================================
# 司机台 PLC 协议（协议 司机驾驶模拟台PLC协议 §2-6）
# ============================================================
PLC_SERVER_ADDR: Final[str] = "192.168.200.123"
PLC_PORT_1: Final[int] = 8001   # 主控
PLC_PORT_2: Final[int] = 8002   # 备用
PLC_PORT_3: Final[int] = 8003   # 备用
PLC_CYCLE_MS: Final[int] = 100  # PLC 发送周期 100ms

PLC_RECV_SIZE: Final[int] = 46   # PLC→上位机
PLC_SEND_SIZE: Final[int] = 26   # 上位机→PLC

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
VISION_LOCAL_ADDR: Final[str] = "192.168.200.110"     # 仿真系统
VISION_REMOTE_ADDR: Final[str] = "192.168.200.102"    # 总控
VISION_REMOTE_PORT: Final[int] = 8303                 # 视景端口
VISION_CYCLE_MS: Final[int] = 100                     # 100ms 周期

# ============================================================
# 司机台显示 TCP（协议 § 网络屏 / 信号屏）
# ============================================================
CAB_DISPLAY_ADDR: Final[str] = "192.168.200.102"      # 总控
CAB_NETWORK_SCREEN_PORT: Final[int] = 8888            # 网络屏
CAB_SIGNAL_SCREEN_PORT: Final[int] = 9999             # 信号屏
CAB_DISPLAY_CYCLE_MS: Final[int] = 100                # 100ms 周期
