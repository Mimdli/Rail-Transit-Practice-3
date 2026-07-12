"""手动测试：向司机台网络屏/信号屏发送画面数据。

用法：
    cd E:\\2025py\\shixun3\\Rail-Transit-Practice-3
    py -3.13 tests\\test_cab_display.py

信号屏(9999) 66 bytes + 网络屏(8888) 572 bytes
通过总控 192.168.200.102 转发至司机台物理屏幕。
"""
import socket
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.network.codec import pack_network_screen, pack_signal_screen

# ---------- 总控转发地址 ----------
RELAY_ADDR = "192.168.200.102"
NETWORK_PORT = 8888   # 网络屏
SIGNAL_PORT = 9999    # 信号屏

# ---------- 测试数据 ----------
# 模拟列车以 65 km/h 运行在正向牵引工况
network_data = {
    "speed": 65.0,           # 速度 65 km/h
    "acceleration": 0.5,     # 加速度 0.5 m/s^2
    "speed_limit": 80.0,     # 限速 80
    "run_mode": 1,           # 运行模式: ATO
    "run_dir": 1,            # 方向: 正向
    "power_pull": 1,         # 牵引
    "net_pressure": 750.0,   # 网压 750V
    "curr_station": 3,       # 当前站
    "next_station": 4,       # 下一站
    "end_station": 15,       # 终点站
    "power_state": 1,        # 供电正常
    "door_states": [1, 1, 1, 1],  # 车门状态
    "has_power": True,
}

signal_data = {
    "speed": 65.0,
    "acceleration": 0.5,
    "speed_limit": 80.0,
    "mode": 1,               # ATO模式
    "run_dir": 1,
    "curr_station": 3,
    "next_station": 4,
    "end_station": 15,
    "pull_switch": 1,        # 牵引手柄拉出
    "pull_state": 1,         # 牵引状态
    "brake_state": 0,
    "urgency_stop": 0,
    "event_id": 0,
    "sig_state": 4,          # 绿灯
    "train_no": 1,
    "next_station_dist": 1250.0,  # 距下一站 1250m
}

# ---------- 打包 ----------
import time
ts = int(time.time() * 1000)
signal_packet = pack_signal_screen(**signal_data, timestamp_ms=ts)
network_packet = pack_network_screen(**network_data, timestamp_ms=ts)

print("=" * 60)
print("  司机台显示 测试")
print("  目标: {} (网络屏:{}, 信号屏:{})".format(RELAY_ADDR, NETWORK_PORT, SIGNAL_PORT))
print("=" * 60)

print()
print("【信号屏报文 ({} bytes)  ==>  {}:{}】".format(len(signal_packet), RELAY_ADDR, SIGNAL_PORT))
for i in range(0, len(signal_packet), 8):
    chunk = signal_packet[i:i+8]
    h = " ".join("{:02X}".format(b) for b in chunk)
    print("  {:03X}:  {}".format(i, h))

print()
print("【网络屏报文 ({} bytes)  ==>  {}:{}】".format(len(network_packet), RELAY_ADDR, NETWORK_PORT))
print("  (仅显示前 64 bytes, 共 572)")
for i in range(0, min(64, len(network_packet)), 8):
    chunk = network_packet[i:i+8]
    h = " ".join("{:02X}".format(b) for b in chunk)
    print("  {:03X}:  {}".format(i, h))
print("  ... ({} bytes total)".format(len(network_packet)))

# ---------- 发送 ----------
def send_one(addr, port, data, label):
    print()
    print("[发送 {}] {}:{} ({} bytes)".format(label, addr, port, len(data)))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((addr, port))
        s.sendall(data)
        s.close()
        print("  [OK] 发送成功")
    except ConnectionRefusedError:
        print("  [ERR] 连接被拒绝 -- 总控或设备未开机")
    except socket.timeout:
        print("  [ERR] 连接超时 -- 请确认 704 网络")
    except Exception as e:
        print("  [ERR] 发送失败: {}".format(e))

send_one(RELAY_ADDR, SIGNAL_PORT, signal_packet, "信号屏 9999")
send_one(RELAY_ADDR, NETWORK_PORT, network_packet, "网络屏 8888")

print()
print("=" * 60)
print("  测试完成")
print("  速度={}km/h, 模式=ATO, 方向=正, 信号=绿灯".format(network_data["speed"]))
print("=" * 60)
