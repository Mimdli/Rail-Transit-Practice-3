"""协议编解码层

提供所有子系统通信报文的打包(pack)和解包(unpack)功能。
所有多字节数值采用小端(Little-Endian)编码，ATP协议除外（大端）。

协议参考：《轨交多系统平台接口协议汇总.md》
"""

import struct
import time as _time
from typing import Optional
from .constants import VEHICLE_UDP_TRAIN_COUNT
from .constants import (
    SIG_SRC_TO_SIGNAL, SIG_DST_TO_SIGNAL,
    SIG_SRC_FROM_SIGNAL, SIG_DST_FROM_SIGNAL,
)


# ============================================================
# 1. 车辆 UDP 编解码（协议 §2）
# ============================================================

def pack_vehicle_udp(
    trains: list[tuple[float, float, float]]
) -> bytes:
    """打包车辆UDP报文：模型→平台

    Args:
        trains: 长度为20的列表，每个元素为 (加速度, 速度, 累计里程)

    Returns:
        480 bytes (20列车 × 3字段 × 8字节 double, 小端)
    """
    buf = bytearray()
    for acc, spd, dist in trains[:VEHICLE_UDP_TRAIN_COUNT]:
        buf.extend(struct.pack("<ddd", acc, spd, dist))
    # 填充不足部分
    remaining = VEHICLE_UDP_TRAIN_COUNT - len(trains)
    for _ in range(remaining):
        buf.extend(struct.pack("<ddd", 0.0, 0.0, 0.0))
    return bytes(buf)


def unpack_vehicle_udp(data: bytes) -> list[tuple[float, float, float]]:
    """解包平台→模型UDP报文

    Returns:
        长度为20的列表，每个元素为 (指令, 百分比)
        指令: 0=惰行, 1=加速, 2=减速
    """
    trains = []
    for i in range(VEHICLE_UDP_TRAIN_COUNT):
        offset = i * 16  # 2 doubles per train
        if offset + 16 > len(data):
            break
        cmd, pct = struct.unpack_from("<dd", data, offset)
        trains.append((cmd, pct))
    return trains


# ============================================================
# 2. 信号系统报文编解码（协议 §3.3）
# ============================================================

def _make_signal_header(src_id: bytes, dst_id: bytes, content_len: int,
                        ctrl: bool = False) -> bytes:
    """构造信号系统通用报文头 (12 bytes)

    Args:
        src_id: 4字节源标识
        dst_id: 4字节目的标识
        content_len: CONTENT 数据长度
        ctrl: True 表示驾驶台开关量报文头 (0xff 0xf1), False 为普通 (0xff 0xf0)

    Returns:
        12字节报文头
    """
    magic = (0xff, 0xf1) if ctrl else (0xff, 0xf0)
    return struct.pack(
        "<" + "BB" + "BBBB" + "BBBB" + "H",
        magic[0], magic[1],        # 固定报文头 (2B)
        src_id[0], src_id[1],      # 源标识 byte 1-2
        src_id[2], src_id[3],      # 源标识 byte 3-4
        dst_id[0], dst_id[1],      # 目的标识 byte 1-2
        dst_id[2], dst_id[3],      # 目的标识 byte 3-4
        2 + content_len,           # 数据长度 = 2 + CONTENT长度
    )


def _make_signal_train_info_header(content_len: int) -> bytes:
    """总控→信号 列车信息报文头"""
    return _make_signal_header(SIG_SRC_TO_SIGNAL, SIG_DST_TO_SIGNAL, content_len)


def _make_signal_switch_signal_header(content_len: int) -> bytes:
    """信号→总控 道岔/信号机状态报文头"""
    return _make_signal_header(SIG_SRC_FROM_SIGNAL, SIG_DST_FROM_SIGNAL, content_len)


def _make_signal_ctrl_to_signal_header(content_len: int) -> bytes:
    """总控→信号 驾驶台开关量报文头 (0xff 0xf1)"""
    return _make_signal_header(SIG_SRC_TO_SIGNAL, SIG_DST_TO_SIGNAL, content_len, ctrl=True)


def _make_signal_ctrl_from_signal_header(content_len: int) -> bytes:
    """信号→总控 驾驶台开关量报文头 (0xff 0xf1)"""
    return _make_signal_header(SIG_SRC_FROM_SIGNAL, SIG_DST_FROM_SIGNAL, content_len, ctrl=True)


def pack_signal_train_info(
    trains: list[dict],
) -> bytes:
    """打包总控→信号系统的列车信息报文

    Args:
        trains: 列车信息列表，每项含:
            id, speed_cms, dist_cm, direction, load_kg,
            fault_speed, emergency_brake, traction_count, brake_count

    Returns:
        完整UDP报文
    """
    content = bytearray()
    for t in trains:
        content.extend(struct.pack(
            "<BIIBIBBB",
            t["id"],                # 1B
            int(t["speed_cms"]),    # 4B  cm/s
            int(t["dist_cm"]),      # 4B  cm
            t.get("direction", 0x55), # 1B
            int(t.get("load_kg", 0)), # 4B
            int(t.get("fault_speed", 0)), # 1B
            1 if t.get("emergency_brake") else 0,  # 1B
            int(t.get("traction_count", 0)),  # 1B
            int(t.get("brake_count", 0)),      # 1B
        ))
    header = _make_signal_train_info_header(len(content))
    return header + bytes(content)


def pack_signal_switch_signal(
    switches: list[tuple[int, int]],
    signals: list[tuple[int, int]],
) -> bytes:
    """打包信号系统→总控的道岔/信号机状态报文 (§3.3.2)

    Args:
        switches: [(编号, 状态), ...]
            状态: 0x00=默认, 0x01=定位, 0x02=反位, 0x04=四开
        signals: [(编号, 灯色), ...]
            灯色: 0x01=红, 0x02=黄, 0x03=红黄, 0x04=绿, ...

    Returns:
        完整UDP报文
    """
    content = bytearray()
    # 道岔数据
    sw_len = len(switches) * 3
    content.extend(struct.pack("<H", sw_len))
    for sid, state in switches:
        content.extend(struct.pack("<HB", sid, state))
    # 信号机数据
    sig_len = len(signals) * 3
    content.extend(struct.pack("<H", sig_len))
    for sid, aspect in signals:
        content.extend(struct.pack("<HB", sid, aspect))
    header = _make_signal_switch_signal_header(len(content))
    return header + bytes(content)


def pack_signal_atp_output(train_id: int, atp_safe: int,
                           atp_unsafe: int, ato_out: int) -> bytes:
    """打包ATP安全输出报文 (§3.3.2.1 CONTENT 仅1车)"""
    content = struct.pack(
        "<BIII",
        train_id & 0xff,
        atp_safe & 0xffffffff,
        atp_unsafe & 0xffffffff,
        ato_out & 0xffffffff,
    )
    header = _make_signal_ctrl_from_signal_header(len(content))
    return header + bytes(content)


# ============================================================
# 3. 司机台 PLC 编解码（协议 司机驾驶模拟台PLC协议 §7）
# ============================================================

# PLC→上位机 46字节报文解析
# 24字节报文头 + 22字节数据区 (11 WORD)
#
# 数据区 WORD 定义 (§7.1):
#   WORD0 (off=24): byte24=指示灯状态, byte25=模式标志
#   WORD1 (off=26): 车辆速度 (WORD)
#   WORD2 (off=28): byte28=按钮/开关, byte29=门控
#   WORD3 (off=30): 外部照明开关状态 (WORD)
#   WORD4 (off=32): 门模式开关状态 (WORD)
#   WORD5 (off=34): byte34=按钮, byte35=开关/钥匙
#   WORD6 (off=36): 方向手柄状态 (WORD 枚举)
#   WORD7 (off=38): 主手柄状态 (WORD 枚举)
#   WORD8 (off=40): 牵引极位 (WORD 0~100)
#   WORD9 (off=42): 制动极位 (WORD 0~100)
#   WORD10 (off=44): 预留

def unpack_plc_data(data: bytes) -> Optional[dict]:
    """解包PLC→上位机报文 (46 bytes)

    Returns:
        dict 包含所有PLC输入状态，或 None 解析失败
    """
    if len(data) < 46:
        return None

    # 24字节报文头 (可校验 _uIdentify=0xAA55AA55, _uTotalLen=46)
    # 数据区从偏移24开始，共22字节 = 11 WORD
    words = struct.unpack_from("<" + "H" * 11, data, 24)

    result = {}

    # WORD0: 指示灯状态 + 模式标志
    w0 = words[0]
    result["indicator_hv_contactor"] = bool(w0 & 0x0002)       # bit1: 高断合指示灯
    result["indicator_brake_release"] = bool(w0 & 0x0004)      # bit2: 制动缓解不良
    result["indicator_door_closed"] = bool(w0 & 0x0020)        # bit5: 门关好
    result["indicator_network_fault"] = bool(w0 & 0x0040)      # bit6: 网络故障
    result["mode_ato_available"] = bool(w0 & 0x0100)           # bit8: 具备ATO
    result["mode_ato_active"] = bool(w0 & 0x0200)              # bit9: ATO激活
    result["mode_ar"] = bool(w0 & 0x1000)                      # bit12: 自动折返

    # WORD1: 车辆速度 (WORD, 单位需根据实机确认)
    result["speed"] = words[1]                                  # 偏移26

    # WORD2: 按钮/开关 + 门控
    w2 = words[2]
    result["btn_emergency_brake"] = bool(w2 & 0x0001)          # bit0: 紧急制动按钮
    result["btn_bus_control"] = bool(w2 & 0x0002)              # bit1: 母线控制
    result["btn_forced_release"] = bool(w2 & 0x0004)           # bit2: 强迫缓解
    result["btn_forced_pump"] = bool(w2 & 0x0008)              # bit3: 强迫泵风
    result["btn_emergency_command"] = bool(w2 & 0x0010)        # bit4: 应急指挥
    result["btn_parking_brake"] = bool(w2 & 0x0020)            # bit5: 停放制动
    result["btn_electric_horn"] = bool(w2 & 0x0040)            # bit6: 电笛
    # byte29: 门控
    result["btn_open_left"] = bool(w2 & 0x0100)                # bit8: 开左门
    result["btn_open_right"] = bool(w2 & 0x0200)               # bit9: 开右门
    result["btn_close_left"] = bool(w2 & 0x0400)               # bit10: 关左门
    result["btn_close_right"] = bool(w2 & 0x0800)              # bit11: 关右门

    # WORD3: 外部照明开关状态
    w3 = words[3]
    result["light_off"] = (w3 & 0x000F) == 0
    result["light_stop"] = bool(w3 & 0x0001)                   # bit0: 停止位
    result["light_auto"] = bool(w3 & 0x0002)                   # bit1: 自动位
    result["light_near"] = bool(w3 & 0x0004)                   # bit2: 近光位
    result["light_far"] = bool(w3 & 0x0008)                    # bit3: 远光位

    # WORD4: 门模式开关状态
    w4 = words[4]
    result["door_mode_semiauto"] = bool(w4 & 0x0001)           # bit0: 半自动
    result["door_mode_manual"] = bool(w4 & 0x0002)             # bit1: 手动
    result["door_mode_auto"] = bool(w4 & 0x0004)               # bit2: 自动

    # WORD5: 按钮 + 开关/钥匙
    w5 = words[5]
    result["btn_high_accel"] = bool(w5 & 0x0001)               # bit0: 高加速
    result["btn_cab_light"] = bool(w5 & 0x0002)                # bit1: 司机室照明
    result["btn_mode_up"] = bool(w5 & 0x0004)                  # bit2: 模式升级
    result["btn_mode_down"] = bool(w5 & 0x0008)                # bit3: 模式降级
    result["btn_confirm"] = bool(w5 & 0x0010)                  # bit4: 确认
    result["btn_ar"] = bool(w5 & 0x0020)                       # bit5: 自动折返
    result["btn_traction_reset"] = bool(w5 & 0x0040)           # bit6: 牵引辅助复位
    result["btn_ato_start"] = bool(w5 & 0x0080)                # bit7: ATO启动
    result["switch_wash"] = bool(w5 & 0x0100)                  # bit8: 洗车模式
    result["master_key"] = bool(w5 & 0x0200)                   # bit9: 司机钥匙
    result["switch_alert"] = bool(w5 & 0x0400)                 # bit10: 警惕
    result["switch_alert_release"] = bool(w5 & 0x0800)         # bit11: 警惕允许解除

    # WORD6: 方向手柄状态 (枚举: 0=零位, 1=向前, 2=向后)
    w6 = words[6]
    result["dir_zero"] = (w6 == 0)
    result["dir_forward"] = (w6 == 1)
    result["dir_backward"] = (w6 == 2)

    # WORD7: 主手柄状态 (枚举: 0=零位, 1=牵引, 2=制动, 4=快速制动)
    w7 = words[7]
    result["handle_zero"] = (w7 == 0)
    result["handle_traction"] = (w7 == 1)
    result["handle_brake"] = (w7 == 2)
    result["handle_fast_brake"] = (w7 == 4)

    # WORD8: 牵引极位 (0~100)
    result["traction_level"] = words[8]

    # WORD9: 制动极位 (0~100)
    result["brake_level"] = words[9]

    # WORD10: 预留
    result["reserved"] = words[10]

    # 原始数据
    result["raw_words"] = list(words)

    # 兼容旧版命名
    result["handle_position"] = result["traction_level"]

    return result


def pack_plc_output(
    indicator_hv_contactor: bool = False,
    indicator_brake_release: bool = False,
    indicator_door_closed: bool = True,
    indicator_network_fault: bool = False,
    mode_ato_available: bool = False,
    mode_ato_active: bool = False,
    mode_ar: bool = False,
    btn_emergency_brake: bool = False,
    btn_forced_release: bool = False,
    btn_forced_pump: bool = False,
    btn_emergency_command: bool = False,
    btn_parking_brake: bool = False,
    btn_open_left: bool = False,
    btn_open_right: bool = False,
    btn_close_left: bool = False,
    btn_close_right: bool = False,
) -> bytes:
    """打包上位机→PLC报文 (26 bytes)

    24字节帧头 + 2字节数据区 (WORD11: ATP安全输出)
    帧头字段按协议填充。
    """
    # WORD11: ATP安全输出 (bitmask)
    atp_safe_out = 0
    if indicator_hv_contactor:
        atp_safe_out |= 1 << 0   # Bit0: 高压接触器指示
    if indicator_brake_release:
        atp_safe_out |= 1 << 1   # Bit1: 制动缓解指示
    if indicator_door_closed:
        atp_safe_out |= 1 << 2   # Bit2: 门关好指示
    if indicator_network_fault:
        atp_safe_out |= 1 << 3   # Bit3: 网络故障指示
    if mode_ato_available:
        atp_safe_out |= 1 << 4   # Bit4: ATO可用
    if mode_ato_active:
        atp_safe_out |= 1 << 5   # Bit5: ATO激活
    if mode_ar:
        atp_safe_out |= 1 << 6   # Bit6: AR模式
    if btn_emergency_brake:
        atp_safe_out |= 1 << 7   # Bit7: 紧急制动
    if btn_forced_release:
        atp_safe_out |= 1 << 8   # Bit8: 强缓
    if btn_forced_pump:
        atp_safe_out |= 1 << 9   # Bit9: 强泵
    if btn_emergency_command:
        atp_safe_out |= 1 << 10  # Bit10: 紧急指令
    if btn_parking_brake:
        atp_safe_out |= 1 << 11  # Bit11: 停放制动
    if btn_open_left:
        atp_safe_out |= 1 << 12  # Bit12: 左门使能
    if btn_open_right:
        atp_safe_out |= 1 << 13  # Bit13: 右门使能
    if btn_close_left:
        atp_safe_out |= 1 << 14  # Bit14: 左门关闭
    if btn_close_right:
        atp_safe_out |= 1 << 15  # Bit15: 右门关闭

    t = _time.localtime(_time.time())
    header = struct.pack("<" + "I" + "H" * 10,
        0xAA55AA55,                      # _uIdentify (4B) 现场PLC发送55 AA 55 AA
        26,                               # _uTotalLen (2B) = 26
        26 - 8,                           # _uDataLen (2B) = 18
        t.tm_year,                        # _uYear
        t.tm_mon,                         # _uMonth
        t.tm_mday,                        # _uDay
        t.tm_hour,                        # _uHour
        t.tm_min,                         # _uMinute
        t.tm_sec,                         # _uSecond
        0,                                # _uVerifyType
        0,                                # _uVerifyCode
    )
    # 2字节数据区 (ATP安全输出)
    data = struct.pack("<H", atp_safe_out & 0xFFFF)
    return header + data


# ============================================================
# 4. ATP DMI 报文编解码（协议 ATP通信协议规范）
# ============================================================

def pack_atp_to_dmi(
    speed_cms: int,
    permit_speed_cms: int,
    ebrake_speed_cms: int,
    target_speed_cms: int,
    target_dist_cm: int,
    mode_current: int,
    mode_max: int,
) -> bytes:
    """打包ATP→DMI报文 (大端)

    报文格式: 4B序列号 + 4B对方序列号 + 2B数据长度 + 129B应用数据 + 6B CRC48
    """
    app_data = bytearray()
    app_data.append(0xAA)                          # 基本信息包标志
    app_data.append(150)                            # 包长度
    app_data.append(0x01)                           # DMI显示状态
    # 时间占位 (6 × 4B)
    app_data.extend(b"\x00" * 24)
    app_data.extend(struct.pack(">H", speed_cms))      # 当前速度
    app_data.extend(struct.pack(">H", permit_speed_cms)) # 允许速度
    app_data.extend(struct.pack(">H", ebrake_speed_cms)) # 紧急制动触发速度
    app_data.extend(struct.pack(">H", target_speed_cms)) # 目标速度
    app_data.extend(b"\x00" * 3)                       # 限速变化点距离 24bit
    app_data.extend(b"\x00" * 3)                       # 目标距离 24bit
    app_data.append(mode_max & 0x0F)                   # 最大可用驾驶模式 4bit
    app_data.append(mode_current & 0x0F)               # 当前驾驶模式 4bit
    # 填充至129字节
    while len(app_data) < 129:
        app_data.append(0x00)

    body = struct.pack(">II", 0, 0) + struct.pack(">H", len(app_data)) + bytes(app_data)
    crc48 = b"\x00" * 6  # CRC48 占位
    return body + crc48


# ============================================================
# 5. 视景系统 TCMS2VIEW 编解码（协议 §3.2）
# ============================================================

FX_SIGNAL_MAX = 77    # 北京地铁9号线正线信号机数
FX_SWITCH_MAX = 29    # 北京地铁9号线正线道岔数
FX_TRAIN_MAX = 128    # 他车最大数


def pack_vision_tcms2view(
    live_counter: int,
    signal_states: list[int],
    switch_states: list[int],
    speed_mms: int,
    accel_pct: int,
    run_state: int,
    position_mm: int,
    edge_id: int,
    direction: int,
    other_trains: list[dict] = None,
) -> bytes:
    """打包视景系统 TCMS2VIEW 报文 (UDP)

    参数:
        live_counter: 数据报计数，每次+1
        signal_states: 信号机状态列表 [0x01/0x02/0x04/0x10/...]
        switch_states: 道岔状态列表 [0x01=定位/0x02=反位]
        speed_mms: 本车速度 (mm/s)
        accel_pct: 本车加速度百分比 (0~100)
        run_state: 运行工况 0x11牵引/0x12制动/0x13惰行
        position_mm: 本车位置相对于起点道岔位移 (mm)
        edge_id: 本车所在边号（区段号）
        direction: 运行方向 1=正方向, -1=反方向
        other_trains: 他车列表 [{dist, edge, dir, speed_cms}, ...]
    """
    buf = bytearray()

    # LiveCounter (int, 4B)
    buf.extend(struct.pack("<i", live_counter))

    # Signal_num + SignalStates
    sig_count = min(len(signal_states), FX_SIGNAL_MAX)
    buf.append(sig_count & 0xFF)
    for i in range(FX_SIGNAL_MAX):
        if i < sig_count:
            buf.append(signal_states[i] & 0xFF)
        else:
            buf.append(0x00)

    # Switch_num + SwitchStates
    sw_count = min(len(switch_states), FX_SWITCH_MAX)
    buf.append(sw_count & 0xFF)
    for i in range(FX_SWITCH_MAX):
        if i < sw_count:
            buf.append(switch_states[i] & 0xFF)
        else:
            buf.append(0x00)

    # 本车信息
    buf.extend(struct.pack("<i", speed_mms))         # Speed, mm/s
    buf.extend(struct.pack("<h", 0))                  # DwellTime, 预留
    buf.append(run_state & 0xFF)                      # RunState
    buf.append(accel_pct & 0xFF)                      # Accel 0~100
    buf.extend(struct.pack("<i", position_mm))        # SectionDistance, mm
    buf.extend(struct.pack("<h", edge_id))            # EdgeID
    buf.append(direction & 0xFF)                      # SectionDirection

    # 他车信息
    others = other_trains or []
    other_count = min(len(others), FX_TRAIN_MAX)
    buf.append(other_count & 0xFF)

    for i in range(FX_TRAIN_MAX):
        if i < other_count:
            t = others[i]
            buf.extend(struct.pack("<i", t.get("dist", 0)))
        else:
            buf.extend(struct.pack("<i", 0))

    for i in range(FX_TRAIN_MAX):
        if i < other_count:
            t = others[i]
            buf.extend(struct.pack("<h", t.get("edge", 0)))
        else:
            buf.extend(struct.pack("<h", 0))

    for i in range(FX_TRAIN_MAX):
        if i < other_count:
            t = others[i]
            buf.append(t.get("dir", 0) & 0xFF)
        else:
            buf.append(0x00)

    for i in range(FX_TRAIN_MAX):
        if i < other_count:
            t = others[i]
            buf.extend(struct.pack("<h", t.get("speed_cms", 0)))
        else:
            buf.extend(struct.pack("<h", 0))

    return bytes(buf)


# ============================================================
# 6. 司机台信号屏 编解码（66 bytes, TCP, 协议 § 信号屏）
# ============================================================

def pack_signal_screen(
    speed: float,
    acceleration: float,
    speed_limit: float,
    mode: int = 5,
    run_dir: int = 0,
    curr_station: int = 0,
    next_station: int = 0,
    end_station: int = 0,
    pull_switch: int = 0,
    pull_state: int = 0,
    brake_state: int = 0,
    urgency_stop: int = 0,
    event_id: int = 0,
    sig_state: int = 0,
    train_no: int = 0,
    next_station_dist: float = 0.0,
    timestamp_ms: int = 0,
) -> bytes:
    """打包信号屏报文 (TCP → 总控:9999)

    协议总长 68 字节（文档66字节，实际字段布局为68）。
    """
    buf = bytearray(68)
    struct.pack_into("<I", buf, 0, 0x55AA55AA)       # _uIdentify
    struct.pack_into("<H", buf, 4, 68)                 # _uTotalLen
    struct.pack_into("<H", buf, 6, 68 - 8)             # _uDataLen
    struct.pack_into("<Q", buf, 8, timestamp_ms)       # _timestamp
    struct.pack_into("<H", buf, 16, 0)                 # _uVerifyType
    struct.pack_into("<H", buf, 18, 0)                 # _uVerifyCode
    struct.pack_into("<H", buf, 20, 0)                 # _uProtocolID
    struct.pack_into("<H", buf, 22, 1)                 # _uMsgID
    # 时间 (24-35)
    import time as _time
    t = _time.localtime(_time.time())
    struct.pack_into("<H", buf, 24, t.tm_year)
    struct.pack_into("<H", buf, 26, t.tm_mon)
    struct.pack_into("<H", buf, 28, t.tm_mday)
    struct.pack_into("<H", buf, 30, t.tm_hour)
    struct.pack_into("<H", buf, 32, t.tm_min)
    struct.pack_into("<H", buf, 34, t.tm_sec)
    # 站信息 (36-43)
    struct.pack_into("<B", buf, 36, curr_station & 0xFF)
    struct.pack_into("<B", buf, 37, next_station & 0xFF)
    struct.pack_into("<B", buf, 38, end_station & 0xFF)
    struct.pack_into("<b", buf, 39, 1)                 # CMState
    struct.pack_into("<b", buf, 40, 1)                 # MMState
    struct.pack_into("<b", buf, 41, 1)                 # CTCState
    struct.pack_into("<b", buf, 42, run_dir & 0xFF)    # RunDir
    struct.pack_into("<B", buf, 43, 0)                 # Reserve
    # 数据字段 (44-67)
    struct.pack_into("<f", buf, 44, speed)             # _nSpeed
    struct.pack_into("<f", buf, 48, acceleration)      # _fAcceleration
    struct.pack_into("<H", buf, 52, pull_switch & 0xFFFF)  # _nPullSwitch
    struct.pack_into("<H", buf, 54, int(speed_limit))       # _fSpeedLimit (WORD)
    struct.pack_into("<B", buf, 56, mode & 0xFF)            # _nMode
    struct.pack_into("<B", buf, 57, pull_state & 0xFF)      # _nPullState
    struct.pack_into("<B", buf, 58, brake_state & 0xFF)     # _nBrakeState
    struct.pack_into("<B", buf, 59, urgency_stop & 0xFF)    # _nUrgencyStopState
    struct.pack_into("<B", buf, 60, event_id & 0xFF)        # _nEventID
    struct.pack_into("<B", buf, 61, sig_state & 0xFF)       # _nSigState
    struct.pack_into("<H", buf, 62, train_no & 0xFFFF)      # _nTrainNo
    struct.pack_into("<f", buf, 64, next_station_dist)      # _fNextStationDist
    return bytes(buf)


# ============================================================
# 7. 司机台网络屏 编解码（572 bytes, TCP, 协议 § 网络屏）
# ============================================================

def pack_network_screen(
    speed: float = 0.0,
    acceleration: float = 0.0,
    position_m: float = 0.0,
    speed_limit: float = 0.0,
    run_mode: int = 0,
    run_dir: int = 0,
    power_pull: int = 0,
    net_pressure: int = 0,
    curr_station: int = 0,
    next_station: int = 0,
    end_station: int = 0,
    power_state: int = 0,
    door_states: list[int] = None,
    has_power: bool = True,
    timestamp_ms: int = 0,
) -> bytes:
    """打包网络屏 572字节报文 (TCP → 总控:8888)

    只填充需要的关键字段，其余填充默认值。
    """
    buf = bytearray(572)
    # 固定头
    struct.pack_into("<I", buf, 0, 0x55AA55AA)       # _uIdentify
    struct.pack_into("<H", buf, 4, 572)                # _uTotalLen
    struct.pack_into("<H", buf, 6, 572 - 8)            # _uDataLen
    struct.pack_into("<Q", buf, 8, timestamp_ms)       # _timestamp
    struct.pack_into("<H", buf, 16, 0)                 # _uVerifyType
    struct.pack_into("<H", buf, 18, 0)                 # _uVerifyCode
    struct.pack_into("<H", buf, 20, 0)                 # _uProtocolID
    struct.pack_into("<H", buf, 22, 2)                 # _uMsgID
    # 时间 (24-35)
    import time as _time
    t = _time.localtime(_time.time())
    struct.pack_into("<H", buf, 24, t.tm_year)
    struct.pack_into("<H", buf, 26, t.tm_mon)
    struct.pack_into("<H", buf, 28, t.tm_mday)
    struct.pack_into("<H", buf, 30, t.tm_hour)
    struct.pack_into("<H", buf, 32, t.tm_min)
    struct.pack_into("<H", buf, 34, t.tm_sec)
    # 站信息 (36-38)
    struct.pack_into("<B", buf, 36, curr_station & 0xFF)
    struct.pack_into("<B", buf, 37, next_station & 0xFF)
    struct.pack_into("<B", buf, 38, end_station & 0xFF)
    # 电源状态 / 速度 / 加速度 / 牵引力 / 网压 (39-51)
    struct.pack_into("<B", buf, 39, power_state & 0xFF)
    struct.pack_into("<f", buf, 40, speed)
    struct.pack_into("<f", buf, 44, acceleration)
    struct.pack_into("<H", buf, 48, int(power_pull) & 0xFFFF)
    struct.pack_into("<H", buf, 50, int(net_pressure) & 0xFFFF)
    # 限速 / 级位 / 模式 (52-55)
    struct.pack_into("<H", buf, 52, int(speed_limit))
    struct.pack_into("<B", buf, 54, 0)                 # _nLevelPos
    struct.pack_into("<B", buf, 55, run_mode & 0xFF)
    # 母线电压 (56-57)
    struct.pack_into("<H", buf, 56, 750 if has_power else 0)  # _nMasterV
    # 方向 / 司机室 (58-59)
    struct.pack_into("<B", buf, 58, run_dir & 0xFF)
    struct.pack_into("<B", buf, 59, 0x11)              # 司机室: TC1激活+TC2未激活
    # 门状态 (60-83, 24 bytes for 6 cars × 4 bytes)
    if door_states:
        for i, ds in enumerate(door_states[:6]):
            struct.pack_into("<I", buf, 60 + i * 4, ds & 0xFFFFFFFF)
    # 载客率 (168-173)
    struct.pack_into("<B", buf, 168, 50)               # 50% 载客率

    return bytes(buf)


# ============================================================
# 8. 辅助工具
# ============================================================

def double_to_cms(value: float) -> int:
    """米/秒 → 厘米/秒"""
    return int(value * 100)


def cms_to_double(value: int) -> float:
    """厘米/秒 → 米/秒"""
    return value / 100.0
