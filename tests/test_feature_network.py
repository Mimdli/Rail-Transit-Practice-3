"""feature 分支补充：ATP 打包与 NetworkManager MODE 行为。"""

from src.network.codec import pack_atp_to_dmi
from src.network.manager import NetworkManager


def test_pack_atp_to_dmi_encodes_target_distance_24bit():
    """target_dist_cm 应写入应用数据中的 24bit 大端字段，而非全零。"""
    dist = 0x010203  # 66051 cm
    pkt = pack_atp_to_dmi(
        speed_cms=1000,
        permit_speed_cms=1200,
        ebrake_speed_cms=1400,
        target_speed_cms=800,
        target_dist_cm=dist,
        mode_current=1,
        mode_max=2,
    )
    # body = 4+4+2 头 + 129 app；app 内速度后为两段 3B 距离
    app = pkt[10:10 + 129]
    # 偏移: 1+1+1+24 + 2*4 = 35 → 限速变化点(全0)；+3 → 目标距离
    assert app[35:38] == b"\x00\x00\x00"
    assert app[38:41] == bytes([0x01, 0x02, 0x03])


def test_pack_atp_to_dmi_clamps_to_24bit():
    pkt = pack_atp_to_dmi(
        speed_cms=0, permit_speed_cms=0, ebrake_speed_cms=0,
        target_speed_cms=0, target_dist_cm=0x1FFFFFF,
        mode_current=0, mode_max=0,
    )
    app = pkt[10:10 + 129]
    assert app[38:41] == bytes([0xFF, 0xFF, 0xFF])


def test_network_manager_local_mode_skips_start_without_force():
    """MODE=local 时无 force_enable 不应真正启动外部连接。"""
    nm = NetworkManager(force_enable=False)
    nm.start()
    assert nm._running is False
    nm.start(force_enable=True)
    # force 后会尝试启动各子模块（可能连不上，但标记为 running）
    assert nm._running is True
    nm.stop()
    assert nm._running is False
