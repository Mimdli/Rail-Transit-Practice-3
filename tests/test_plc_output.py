"""手动测试：向上位机→司机台PLC发送一帧输出报文。

用法：
    cd E:\\2025py\\shixun3\\Rail-Transit-Practice-3
    py -3.13 tests\\test_plc_output.py

发送内容说明：
    26字节报文 = 24字节帧头 + 2字节数据区(WORD11: ATP安全输出)

    默认测试参数（模拟正常行驶状态）：
      - 门关好指示 = True        → Bit2 = 1
      - 其余指示灯/按钮 = False  → 对应位 = 0

    这会让司机台看到：门关好指示灯亮起，其他灯熄灭。
"""
import socket
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.network.codec import pack_plc_output

# ---------- 测试参数 ----------
# （切换到 704 实验室网络时，确认 IP 和端口配置）
PLC_ADDR = "192.168.100.123"
PLC_PORT = 8001

# 发送指示灯状态：
#   door_closed=True → 司机台「门关好」灯亮
#   其他保持默认 False
test_params = {
    "indicator_door_closed": True,   # 门关好
}

# ---------- 打包 ----------
packet = pack_plc_output(**test_params)
assert len(packet) == 26, f"报文长度错误: {len(packet)}"

# ---------- 打印详细内容 ----------
print("=" * 60)
print("  司机台 PLC 输出测试")
print("  目标: {}:{}".format(PLC_ADDR, PLC_PORT))
print("=" * 60)
print()
print("【测试参数】")
for k, v in {
    "indicator_hv_contactor": "高压接触器指示 (Bit0)",
    "indicator_brake_release": "制动缓解指示   (Bit1)",
    "indicator_door_closed":   "门关好指示     (Bit2)",
    "indicator_network_fault": "网络故障指示   (Bit3)",
    "mode_ato_available":      "ATO可用       (Bit4)",
    "mode_ato_active":         "ATO激活       (Bit5)",
    "mode_ar":                 "AR模式        (Bit6)",
    "btn_emergency_brake":     "紧急制动      (Bit7)",
    "btn_forced_release":      "强缓          (Bit8)",
    "btn_forced_pump":         "强泵          (Bit9)",
    "btn_emergency_command":   "紧急指令      (Bit10)",
    "btn_parking_brake":       "停放制动      (Bit11)",
    "btn_open_left":           "左门使能      (Bit12)",
    "btn_open_right":          "右门使能      (Bit13)",
    "btn_close_left":          "左门关闭      (Bit14)",
    "btn_close_right":         "右门关闭      (Bit15)",
}.items():
    val = test_params.get(k, False)
    print("  {} = {}  // {}".format(k, str(val).ljust(5), v))

print()
print("【WORD11 ATP安全输出 (2 bytes)】")
word11 = int.from_bytes(packet[24:26], "little")
print("  值: 0x{:04X}  ({} bits)".format(word11, bin(word11).count("1")))

print()
print("【完整报文 HEX dump (26 bytes)】")
for i in range(0, 26, 4):
    chunk = packet[i:i + 4]
    hex_str = " ".join("{:02X}".format(b) for b in chunk)
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    print("  {:02X}:  {:<18s}  {}".format(i, hex_str, ascii_str))

print()
print("  帧头标识 (bytes 0-3):  {}  ==> 识别为 {}  ==> {}".format(
    " ".join("{:02X}".format(b) for b in packet[0:4]),
    "0xAA55AA55",
    "OK" if packet[0:4] == b'\x55\xAA\x55\xAA' else "ERR"
))
print("  总长         (bytes 4-5):  {} ==> {}".format(
    int.from_bytes(packet[4:6], "little"),
    "OK (26)" if int.from_bytes(packet[4:6], "little") == 26 else "ERR"
))
print("  数据长       (bytes 6-7):  {} ==> {}".format(
    int.from_bytes(packet[6:8], "little"),
    "OK (18)" if int.from_bytes(packet[6:8], "little") == 18 else "ERR"
))

# ---------- 发送 ----------
print()
print("【发送中…】")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    sock.connect((PLC_ADDR, PLC_PORT))
    sock.sendall(packet)
    print("  [OK] 发送成功！{} 字节已发送至 {}:{}".format(
        len(packet), PLC_ADDR, PLC_PORT))

    # 试着接收一下回包
    try:
        sock.settimeout(1)
        recv_data = sock.recv(46)
        if recv_data:
            print()
            print("【收到 PLC 回包 ({} bytes)】".format(len(recv_data)))
            for i in range(0, min(len(recv_data), 46), 4):
                chunk = recv_data[i:i + 4]
                hex_str = " ".join("{:02X}".format(b) for b in chunk)
                print("  {:02X}:  {}".format(i, hex_str))
    except socket.timeout:
        print("  (未收到回包，正常)")
    except Exception as e:
        print("  接收回包异常: {}".format(e))

    sock.close()
except ConnectionRefusedError:
    print("  [ERR] 连接被拒绝 -- PLC 设备未开机或端口不对")
except socket.timeout:
    print("  [ERR] 连接超时 -- 请确认 704 网络是否连通")
except Exception as e:
    print("  [ERR] 发送失败: {}".format(e))

print()
print("=" * 60)
print("  测试完成")
print("=" * 60)
