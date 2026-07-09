"""协议编解码层

提供所有子系统通信报文的打包(pack)和解包(unpack)功能。
所有多字节数值采用小端(Little-Endian)编码，ATP协议除外（大端）。

协议参考：《轨交多系统平台接口协议汇总.md》
"""

import struct
from typing import Optional
from .constants import VEHICLE_UDP_TRAIN_COUNT


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

def _make_signal_header(dest: int, src: int, content_len: int) -> bytes:
    """构造信号系统通用报文头 (12 bytes)"""
    return struct.pack(
        "<" + "B" * 10 + "H",
        0xff, 0xf0,          # 固定报文头
        src & 0xff, 0x00,    # 源标识 byte 1 + pad
        src & 0xff, 0x00,    # 源标识 byte 3 + pad
        dest & 0xff, 0x00,   # 目的标识 byte 1 + pad
        dest & 0xff, 0x00,   # 目的标识 byte 3 + pad
        2 + content_len,     # 数据长度 = 2 + CONTENT长度
    )


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
    header = _make_signal_header(0x00, 0x01, len(content))
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
    sw_len = 2 + len(switches) * 3
    content.extend(struct.pack("<H", sw_len))
    for sid, state in switches:
        content.extend(struct.pack("<HB", sid, state))
    # 信号机数据
    sig_len = 2 + len(signals) * 3
    content.extend(struct.pack("<H", sig_len))
    for sid, aspect in signals:
        content.extend(struct.pack("<HB", sid, aspect))
    header = _make_signal_header(0x10, 0x00, len(content))
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
    header = _make_signal_header(0x10, 0x00, len(content))
    return header + bytes(content)


# ============================================================
# 3. 司机台 PLC 编解码（协议 司机驾驶模拟台PLC协议 §7）
# ============================================================

# PLC→上位机 46字节报文解析模板
# 24字节报文头 + 22字节数据区

def unpack_plc_data(data: bytes) -> Optional[dict]:
    """解包PLC→上位机报文 (46 bytes)

    Returns:
        dict 包含所有PLC输入状态，或 None 解析失败
    """
    if len(data) < 46:
        return None

    result = {}

    # 数据区从偏移24开始，共22字节 = 11 WORD
    # 按照协议 §7.1 字段定义逐位解析
    words = struct.unpack_from("<" + "H" * 11, data, 24)

    # WORD0: 司控器手柄
    w0 = words[0]
    result["handle_position"] = w0 & 0x7F          # bit0-6: 手柄级位
    result["handle_zero"] = bool(w0 & 0x0080)       # bit7: 零位

    # WORD1: 方向开关
    w1 = words[1]
    result["dir_forward"] = bool(w1 & 0x0001)       # bit0: 向前
    result["dir_backward"] = bool(w1 & 0x0002)      # bit1: 向后
    result["master_key"] = bool(w1 & 0x0004)        # bit2: 司机钥匙

    # WORD2: 门控按钮
    w2 = words[2]
    result["btn_open_left"] = bool(w2 & 0x0001)
    result["btn_close_left"] = bool(w2 & 0x0002)
    result["btn_open_right"] = bool(w2 & 0x0004)
    result["btn_close_right"] = bool(w2 & 0x0008)

    # WORD3: ATO按钮
    w3 = words[3]
    result["ato_start"] = bool(w3 & 0x0001)
    result["mode_up"] = bool(w3 & 0x0002)
    result["mode_down"] = bool(w3 & 0x0004)
    result["ar_button"] = bool(w3 & 0x0008)

    # WORD4: 报警/状态
    w4 = words[4]
    result["emergency_brake"] = bool(w4 & 0x0001)
    result["eum_mode"] = bool(w4 & 0x0002)

    # WORD5-10: 预留/备用
    result["raw_words"] = list(words)

    return result


def pack_plc_output(
    atp_safe_out: int = 0,
    atp_unsafe_out: int = 0,
    ato_out: int = 0,
) -> bytes:
    """打包上位机→PLC报文 (26 bytes)

    24字节帧头 + 2字节数据区
    """
    # 24字节帧头（固定格式）
    header = struct.pack("<" + "H" * 12,
        0x0000, 0x0000, 0x0000, 0x0000,
        0x0000, 0x0000, 0x0000, 0x0000,
        0x0000, 0x0000, 0x0000, 0x0000,
    )
    # 2字节数据区
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
# 5. 辅助工具
# ============================================================

def double_to_cms(value: float) -> int:
    """米/秒 → 厘米/秒"""
    return int(value * 100)


def cms_to_double(value: int) -> float:
    """厘米/秒 → 米/秒"""
    return value / 100.0
