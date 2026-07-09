"""单车力计算单元测试 — 阶段 5

每种力 ≥5 个测试用例，与手算结果比对，误差 < 1%。
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.car_config import MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG
from src.vehicle.forces import (
    calc_davis_resistance,
    calc_grade_resistance,
    calc_tunnel_resistance,
    calc_total_resistance,
    calc_tractive_force,
    calc_brake_force,
)


MOTOR = MOTOR_CAR_CONFIG    # mass=60000, A=1200, B=30, C=3
TRAILER = TRAILER_CAR_CONFIG  # mass=46000, A=900, B=25, C=2.5


# ═══════════════════════════════════════════════════════════════
# calc_davis_resistance
# ═══════════════════════════════════════════════════════════════

def test_davis_v0():
    """v=0: R = 1200 → -1200 N"""
    f = calc_davis_resistance(MOTOR, 0.0)
    assert abs(f - (-1200.0)) < 1.0, f"Expected -1200, got {f}"


def test_davis_v10():
    """v=10: R = 1200+300+300 = 1800 → -1800 N"""
    f = calc_davis_resistance(MOTOR, 10.0)
    expected = -(1200 + 30*10 + 3*100)
    assert abs(f - expected) < abs(expected) * 0.01, f"Expected {expected}, got {f}"


def test_davis_v20():
    """v=20: R = 1200+600+1200 = 3000 → -3000 N"""
    f = calc_davis_resistance(MOTOR, 20.0)
    expected = -(1200 + 30*20 + 3*400)
    assert abs(f - expected) < abs(expected) * 0.01


def test_davis_v22_22():
    """v=22.22: 阻力正确。"""
    f = calc_davis_resistance(MOTOR, 22.22)
    v = 22.22
    expected = -(1200 + 30*v + 3*v*v)
    assert abs(f - expected) < abs(expected) * 0.01


def test_davis_v30():
    """v=30: R = 1200+900+2700 = 4800 → -4800 N"""
    f = calc_davis_resistance(MOTOR, 30.0)
    expected = -(1200 + 30*30 + 3*900)
    assert abs(f - expected) < abs(expected) * 0.01


def test_davis_trailer_v20():
    """拖车 v=20: R = 900+500+1000 = 2400 → -2400 N"""
    f = calc_davis_resistance(TRAILER, 20.0)
    expected = -(900 + 25*20 + 2.5*400)
    assert abs(f - expected) < abs(expected) * 0.01


def test_davis_negative_velocity():
    """负速度时阻力方向反转。"""
    f = calc_davis_resistance(MOTOR, -10.0)
    # magnitude 相同但符号相反
    assert f > 0


# ═══════════════════════════════════════════════════════════════
# calc_grade_resistance
# ═══════════════════════════════════════════════════════════════

def test_grade_zero():
    """坡度 0: F = 0"""
    assert calc_grade_resistance(MOTOR, 0.0) == 0.0


def test_grade_uphill_30():
    """上坡 30‰: F = -60000*9.81*0.03 = -17658 N"""
    f = calc_grade_resistance(MOTOR, 30.0)
    expected = -60000 * 9.81 * 0.03
    assert abs(f - expected) < abs(expected) * 0.01


def test_grade_downhill_15():
    """下坡 -15‰: F = +8829 N (助力)"""
    f = calc_grade_resistance(MOTOR, -15.0)
    expected = 60000 * 9.81 * 0.015
    assert abs(f - expected) < abs(expected) * 0.01


def test_grade_uphill_10():
    """上坡 10‰: F = -5886 N"""
    f = calc_grade_resistance(MOTOR, 10.0)
    expected = -60000 * 9.81 * 0.01
    assert abs(f - expected) < abs(expected) * 0.01


def test_grade_trailer_uphill_30():
    """拖车上坡 30‰: F = -46000*9.81*0.03 = -13537.8 N"""
    f = calc_grade_resistance(TRAILER, 30.0)
    expected = -46000 * 9.81 * 0.03
    assert abs(f - expected) < abs(expected) * 0.01


def test_grade_trailer_downhill_5():
    """拖车下坡 -5‰: F = +2256.3 N"""
    f = calc_grade_resistance(TRAILER, -5.0)
    expected = 46000 * 9.81 * 0.005
    assert abs(f - expected) < abs(expected) * 0.01


# ═══════════════════════════════════════════════════════════════
# calc_tunnel_resistance
# ═══════════════════════════════════════════════════════════════

def test_tunnel_false():
    """非隧道：返回 0"""
    assert calc_tunnel_resistance(MOTOR, 20.0, False) == 0.0


def test_tunnel_v20():
    """隧道 v=20: Davis=3000, extra=3000*0.3=900 → -900 N"""
    f = calc_tunnel_resistance(MOTOR, 20.0, True)
    davis = 1200 + 30*20 + 3*400  # 3000
    expected = -davis * 0.3
    assert abs(f - expected) < abs(expected) * 0.01


def test_tunnel_v0():
    """隧道 v=0: Davis=1200, extra=360 → -360 N"""
    f = calc_tunnel_resistance(MOTOR, 0.0, True)
    expected = -1200 * 0.3
    assert abs(f - expected) < abs(expected) * 0.01


def test_tunnel_v10():
    """隧道 v=10: Davis=1800, extra=540 → -540 N"""
    f = calc_tunnel_resistance(MOTOR, 10.0, True)
    davis = 1200 + 30*10 + 3*100  # 1800
    expected = -davis * 0.3
    assert abs(f - expected) < abs(expected) * 0.01


def test_tunnel_trailer_v20():
    """拖车隧道 v=20: Davis=2400, extra=720 → -720 N"""
    f = calc_tunnel_resistance(TRAILER, 20.0, True)
    davis = 900 + 25*20 + 2.5*400  # 2400
    expected = -davis * 0.3
    assert abs(f - expected) < abs(expected) * 0.01


# ═══════════════════════════════════════════════════════════════
# calc_total_resistance
# ═══════════════════════════════════════════════════════════════

def test_total_flat_no_tunnel():
    """v=20, 0‰, 无隧道: -3000 + 0 + 0 = -3000 N"""
    f = calc_total_resistance(MOTOR, 20.0, 0.0, False)
    expected = -3000.0
    assert abs(f - expected) < abs(expected) * 0.01


def test_total_uphill_tunnel():
    """v=20, +30‰, 隧道: -3000 + (-17658) + (-900) = -21558 N"""
    f = calc_total_resistance(MOTOR, 20.0, 30.0, True)
    expected_davis = -(1200 + 30*20 + 3*400)       # -3000
    expected_grade = -60000 * 9.81 * 0.03            # -17658
    expected_tunnel = -(1200 + 30*20 + 3*400) * 0.3  # -900
    expected = expected_davis + expected_grade + expected_tunnel
    assert abs(f - expected) < abs(expected) * 0.01


def test_total_downhill_no_tunnel():
    """v=20, -15‰, 无隧道: -3000 + 8829 + 0 = 5829 N (下坡助力大于阻力)"""
    f = calc_total_resistance(MOTOR, 20.0, -15.0, False)
    expected = -3000.0 + 60000 * 9.81 * 0.015
    assert abs(f - expected) < abs(expected) * 0.01


def test_total_v0():
    """v=0, 0‰, 无隧道: -1200 N (仅静态阻力)"""
    f = calc_total_resistance(MOTOR, 0.0, 0.0, False)
    assert abs(f - (-1200.0)) < 1.0


def test_total_trailer():
    """拖车 v=20, +30‰, 隧道: 各分量正确"""
    f = calc_total_resistance(TRAILER, 20.0, 30.0, True)
    d = -(900 + 25*20 + 2.5*400)           # -2400
    g = -46000 * 9.81 * 0.03                # -13537.8
    t = -(900 + 25*20 + 2.5*400) * 0.3      # -720
    expected = d + g + t
    assert abs(f - expected) < abs(expected) * 0.01


# ═══════════════════════════════════════════════════════════════
# calc_tractive_force
# ═══════════════════════════════════════════════════════════════

def test_traction_constant_torque():
    """v=5 (< 10 恒力矩), throttle=1.0, dry: F = 80000 N"""
    f, limited = calc_tractive_force(MOTOR, 5.0, 1.0, 0.18)
    assert abs(f - 80000.0) < 1.0
    assert not limited


def test_traction_constant_power():
    """v=15 (恒功率), throttle=1.0: F = 80000*10/15 = 53333.33 N"""
    f, limited = calc_tractive_force(MOTOR, 15.0, 1.0, 0.18)
    expected = 80000 * 10 / 15
    assert abs(f - expected) < expected * 0.01


def test_traction_near_construction_speed():
    """v=22.0 (< 22.22 构造速度): F = 80000*10/22.0 ≈ 36363.6 N"""
    f, limited = calc_tractive_force(MOTOR, 22.0, 1.0, 0.18)
    expected = 80000 * 10 / 22.0
    assert abs(f - expected) < expected * 0.01


def test_traction_exactly_at_construction_speed():
    """v=22.22 (= 构造速度): F = 0（牵引力自然衰减至零）"""
    f, limited = calc_tractive_force(MOTOR, 22.22, 1.0, 0.18)
    assert f == 0.0
    assert not limited


def test_traction_above_construction_speed():
    """v=25 (> 22.22): F = 0"""
    f, limited = calc_tractive_force(MOTOR, 25.0, 1.0, 0.18)
    assert f == 0.0


def test_traction_half_throttle():
    """v=5, throttle=0.5: F = 40000 N"""
    f, limited = calc_tractive_force(MOTOR, 5.0, 0.5, 0.18)
    assert abs(f - 40000.0) < 1.0


def test_traction_adhesion_limit_rain():
    """v=5, throttle=1.0, rain(0.10): 黏着限制 58860 N"""
    f, limited = calc_tractive_force(MOTOR, 5.0, 1.0, 0.10)
    adhesion_limit = 0.10 * 60000 * 9.81  # 58860
    assert abs(f - adhesion_limit) < 1.0
    assert limited  # 被黏着限制截断


def test_traction_adhesion_limit_snow():
    """v=5, throttle=1.0, snow(0.06): 黏着限制 35316 N"""
    f, limited = calc_tractive_force(MOTOR, 5.0, 1.0, 0.06)
    adhesion_limit = 0.06 * 60000 * 9.81  # 35316
    assert abs(f - adhesion_limit) < 1.0
    assert limited


def test_traction_trailer_zero():
    """拖车无动力: F = 0"""
    f, limited = calc_tractive_force(TRAILER, 5.0, 1.0, 0.18)
    assert f == 0.0
    assert not limited


def test_traction_zero_throttle():
    """throttle=0: F = 0"""
    f, limited = calc_tractive_force(MOTOR, 10.0, 0.0, 0.18)
    assert f == 0.0
    assert not limited


# ═══════════════════════════════════════════════════════════════
# calc_brake_force
# ═══════════════════════════════════════════════════════════════

def test_brake_half():
    """v=20, brake=0.5, dry: max_brake=80000+(100000-80000)*0.5=90000, mag=90000 → -90000 N

    P0 修复: 去除二次乘法，max_brake 已是插值结果，不再乘 brake_level。
    """
    f, limited = calc_brake_force(MOTOR, 20.0, 0.5, 0.18)
    max_brake = 80000 + (100000 - 80000) * 0.5  # 90000
    assert abs(f - (-max_brake)) < 1.0, f"Expected {-max_brake}, got {f}"
    assert not limited


def test_brake_full_emergency():
    """v=20, brake=1.0, dry: max=100000, mag=100000 → -100000 N"""
    f, limited = calc_brake_force(MOTOR, 20.0, 1.0, 0.18)
    assert abs(f - (-100000.0)) < 1.0


def test_brake_stopped():
    """v=0: F = 0"""
    f, limited = calc_brake_force(MOTOR, 0.0, 1.0, 0.18)
    assert f == 0.0


def test_brake_zero_level():
    """brake=0: F = 0"""
    f, limited = calc_brake_force(MOTOR, 20.0, 0.0, 0.18)
    assert f == 0.0


def test_brake_adhesion_limit_rain():
    """v=20, brake=1.0, rain(0.10): 黏着限制 58860 → -58860 N"""
    f, limited = calc_brake_force(MOTOR, 20.0, 1.0, 0.10)
    adhesion_limit = 0.10 * 60000 * 9.81  # 58860
    assert abs(f - (-adhesion_limit)) < 1.0
    assert limited  # 被黏着限制截断


def test_brake_trailer():
    """拖车制动也能产生制动力。"""
    f, limited = calc_brake_force(TRAILER, 20.0, 1.0, 0.18)
    # max=65000+(80000-65000)*1=80000, mag=80000, adhesion_limit=0.18*46000*9.81=81226.8
    # 80000 ≤ 81226.8, so f = -80000
    assert abs(f - (-80000.0)) < 1.0


# ═══════════════════════════════════════════════════════════════
# 曲线附加阻力
# ═══════════════════════════════════════════════════════════════

from src.vehicle.forces import calc_curve_resistance


def test_curve_resistance_straight():
    """直线 (radius=None): 阻力 = 0"""
    assert calc_curve_resistance(MOTOR, None) == 0.0
    assert calc_curve_resistance(MOTOR, 0.0) == 0.0


def test_curve_resistance_r400():
    """R=400m: R_curve = 700/400 × 60000 × 9.81 = 1030050 → -1030050 N"""
    f = calc_curve_resistance(MOTOR, 400.0)
    expected = 700.0 / 400.0 * 60000 * 9.81
    assert abs(f - (-expected)) < expected * 0.01, f"Expected {-expected}, got {f}"


def test_curve_resistance_r800():
    """R=800m (大半径): 阻力较小"""
    f = calc_curve_resistance(MOTOR, 800.0)
    expected = 700.0 / 800.0 * 60000 * 9.81
    assert abs(f - (-expected)) < expected * 0.01


def test_curve_resistance_r200():
    """R=200m (小半径急弯): 阻力较大"""
    f = calc_curve_resistance(MOTOR, 200.0)
    expected = 700.0 / 200.0 * 60000 * 9.81
    assert abs(f - (-expected)) < expected * 0.01


def test_curve_resistance_trailer():
    """拖车曲线阻力与质量成正比。"""
    f_motor = calc_curve_resistance(MOTOR, 400.0)
    f_trailer = calc_curve_resistance(TRAILER, 400.0)
    ratio = abs(f_trailer) / abs(f_motor)
    expected_ratio = TRAILER.mass / MOTOR.mass
    assert abs(ratio - expected_ratio) < 0.01


def test_total_resistance_includes_curve():
    """calc_total_resistance 包含曲线阻力。"""
    from src.vehicle.forces import calc_total_resistance
    f_flat = calc_total_resistance(MOTOR, 20.0, 0.0, False, None)
    f_curve = calc_total_resistance(MOTOR, 20.0, 0.0, False, 400.0)
    # 曲线上的总阻力应该更大（更负）
    assert f_curve < f_flat, f"曲线阻力应使总阻力更大: flat={f_flat:.1f}, curve={f_curve:.1f}"


# ═══════════════════════════════════════════════════════════════
# 电空混合制动
# ═══════════════════════════════════════════════════════════════

from src.vehicle.forces import (
    calc_electric_brake_magnitude,
    calc_friction_brake_magnitude,
    _calc_raw_brake_magnitude,
)


def test_electric_brake_motor_only():
    """只有动车有电气制动。"""
    e_motor = calc_electric_brake_magnitude(MOTOR, 20.0, 1.0)
    e_trailer = calc_electric_brake_magnitude(TRAILER, 20.0, 1.0)
    assert e_motor > 0, "动车应有电气制动"
    assert e_trailer == 0.0, "拖车无电气制动"


def test_electric_brake_fades_at_low_speed():
    """v < 5 km/h 时电气制动线性衰减。"""
    # v = 20 m/s = 72 km/h → fade_ratio = 1.0
    e_high = calc_electric_brake_magnitude(MOTOR, 20.0, 1.0)
    # v = 0.5 m/s = 1.8 km/h → fade_ratio = 1.8/5.0 = 0.36
    e_low = calc_electric_brake_magnitude(MOTOR, 0.5, 1.0)
    assert e_low < e_high, f"低速应衰减: high={e_high:.1f}, low={e_low:.1f}"


def test_electric_brake_fade_zero_at_stop():
    """v=0 时电气制动完全消失。"""
    e_stop = calc_electric_brake_magnitude(MOTOR, 0.0, 1.0)
    assert e_stop == 0.0


def test_friction_brake_compensates_fade():
    """电气制动衰减时，空气制动补偿差额。"""
    brake_level = 1.0
    raw_total = _calc_raw_brake_magnitude(MOTOR, brake_level)

    # 高速: 电气 ≈ 0.7 * raw_total, 空气 ≈ 0.3 * raw_total
    e_high = calc_electric_brake_magnitude(MOTOR, 20.0, brake_level)
    f_high = calc_friction_brake_magnitude(MOTOR, brake_level, e_high)
    assert abs(e_high + f_high - raw_total) < 1.0, "电空总和应等于总需求"

    # 低速: 电气衰减, 空气增大补偿
    e_low = calc_electric_brake_magnitude(MOTOR, 0.5, brake_level)
    f_low = calc_friction_brake_magnitude(MOTOR, brake_level, e_low)
    assert abs(e_low + f_low - raw_total) < 1.0, "电空总和应始终等于总需求"
    assert f_low > f_high, "低速时空气制动应补偿电气衰减"


def test_total_brake_adhesion_applied_to_sum():
    """黏着限制对总制动力统一施加（非分别施加）。"""
    # 雨天: adhesion_limit = 0.10 * 60000 * 9.81 = 58860 N
    # 紧急制动需求 = 100000 N > 58860 → 应被限幅
    f_total, limited = calc_brake_force(MOTOR, 20.0, 1.0, 0.10)
    adhesion_limit = 0.10 * 60000 * 9.81
    assert abs(f_total - (-adhesion_limit)) < 1.0, (
        f"总制动力应=黏着限制: expected {-adhesion_limit}, got {f_total}"
    )
    assert limited


def test_brake_load_compensation_with_ep():
    """载荷补偿在电空制动中正确生效。"""
    from src.common.car_config import CarConfig

    car_heavy = CarConfig(
        name="TestHeavy", mass=67200.0, length=19.5, is_motor=True,
        max_service_brake_force=80000.0, max_emergency_brake_force=100000.0,
        base_mass=60000.0,
    )
    f_heavy, _ = calc_brake_force(car_heavy, 20.0, 1.0, 0.18)
    f_normal, _ = calc_brake_force(MOTOR, 20.0, 1.0, 0.18)
    # 重载制动力 = 基准 × (67200/60000) = 1.12x
    assert abs(f_heavy) > abs(f_normal), (
        f"重载制动力应更大: normal={f_normal:.1f}, heavy={f_heavy:.1f}"
    )


if __name__ == "__main__":
    # Davis
    test_davis_v0()
    test_davis_v10()
    test_davis_v20()
    test_davis_v22_22()
    test_davis_v30()
    test_davis_trailer_v20()
    test_davis_negative_velocity()
    # Grade
    test_grade_zero()
    test_grade_uphill_30()
    test_grade_downhill_15()
    test_grade_uphill_10()
    test_grade_trailer_uphill_30()
    test_grade_trailer_downhill_5()
    # Tunnel
    test_tunnel_false()
    test_tunnel_v20()
    test_tunnel_v0()
    test_tunnel_v10()
    test_tunnel_trailer_v20()
    # Total resistance
    test_total_flat_no_tunnel()
    test_total_uphill_tunnel()
    test_total_downhill_no_tunnel()
    test_total_v0()
    test_total_trailer()
    # Tractive
    test_traction_constant_torque()
    test_traction_constant_power()
    test_traction_near_construction_speed()
    test_traction_exactly_at_construction_speed()
    test_traction_above_construction_speed()
    test_traction_half_throttle()
    test_traction_adhesion_limit_rain()
    test_traction_adhesion_limit_snow()
    test_traction_trailer_zero()
    test_traction_zero_throttle()
    # Brake
    test_brake_half()
    test_brake_full_emergency()
    test_brake_stopped()
    test_brake_zero_level()
    test_brake_adhesion_limit_rain()
    test_brake_trailer()
    # Curve resistance
    test_curve_resistance_straight()
    test_curve_resistance_r400()
    test_curve_resistance_r800()
    test_curve_resistance_r200()
    test_curve_resistance_trailer()
    test_total_resistance_includes_curve()
    # Electro-pneumatic brake
    test_electric_brake_motor_only()
    test_electric_brake_fades_at_low_speed()
    test_electric_brake_fade_zero_at_stop()
    test_friction_brake_compensates_fade()
    test_total_brake_adhesion_applied_to_sum()
    test_brake_load_compensation_with_ep()
    print("All forces tests passed!")
