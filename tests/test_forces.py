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
    f = calc_tractive_force(MOTOR, 5.0, 1.0, 0.18)
    assert abs(f - 80000.0) < 1.0


def test_traction_constant_power():
    """v=15 (恒功率), throttle=1.0: F = 80000*10/15 = 53333.33 N"""
    f = calc_tractive_force(MOTOR, 15.0, 1.0, 0.18)
    expected = 80000 * 10 / 15
    assert abs(f - expected) < expected * 0.01


def test_traction_near_construction_speed():
    """v=22.0 (< 22.22 构造速度): F = 80000*10/22.0 ≈ 36363.6 N"""
    f = calc_tractive_force(MOTOR, 22.0, 1.0, 0.18)
    expected = 80000 * 10 / 22.0
    assert abs(f - expected) < expected * 0.01


def test_traction_exactly_at_construction_speed():
    """v=22.22 (= 构造速度): F = 0（牵引力自然衰减至零）"""
    f = calc_tractive_force(MOTOR, 22.22, 1.0, 0.18)
    assert f == 0.0


def test_traction_above_construction_speed():
    """v=25 (> 22.22): F = 0"""
    f = calc_tractive_force(MOTOR, 25.0, 1.0, 0.18)
    assert f == 0.0


def test_traction_half_throttle():
    """v=5, throttle=0.5: F = 40000 N"""
    f = calc_tractive_force(MOTOR, 5.0, 0.5, 0.18)
    assert abs(f - 40000.0) < 1.0


def test_traction_adhesion_limit_rain():
    """v=5, throttle=1.0, rain(0.10): 黏着限制 58860 N"""
    f = calc_tractive_force(MOTOR, 5.0, 1.0, 0.10)
    adhesion_limit = 0.10 * 60000 * 9.81  # 58860
    assert abs(f - adhesion_limit) < 1.0


def test_traction_adhesion_limit_snow():
    """v=5, throttle=1.0, snow(0.06): 黏着限制 35316 N"""
    f = calc_tractive_force(MOTOR, 5.0, 1.0, 0.06)
    adhesion_limit = 0.06 * 60000 * 9.81  # 35316
    assert abs(f - adhesion_limit) < 1.0


def test_traction_trailer_zero():
    """拖车无动力: F = 0"""
    f = calc_tractive_force(TRAILER, 5.0, 1.0, 0.18)
    assert f == 0.0


def test_traction_zero_throttle():
    """throttle=0: F = 0"""
    f = calc_tractive_force(MOTOR, 10.0, 0.0, 0.18)
    assert f == 0.0


# ═══════════════════════════════════════════════════════════════
# calc_brake_force
# ═══════════════════════════════════════════════════════════════

def test_brake_half():
    """v=20, brake=0.5, dry: max_brake=90000, magnitude=45000 → -45000 N"""
    f = calc_brake_force(MOTOR, 20.0, 0.5, 0.18)
    max_brake = 80000 + (100000 - 80000) * 0.5  # 90000
    magnitude = max_brake * 0.5                    # 45000
    assert abs(f - (-magnitude)) < 1.0


def test_brake_full_emergency():
    """v=20, brake=1.0, dry: max=100000, mag=100000 → -100000 N"""
    f = calc_brake_force(MOTOR, 20.0, 1.0, 0.18)
    assert abs(f - (-100000.0)) < 1.0


def test_brake_stopped():
    """v=0: F = 0"""
    f = calc_brake_force(MOTOR, 0.0, 1.0, 0.18)
    assert f == 0.0


def test_brake_zero_level():
    """brake=0: F = 0"""
    f = calc_brake_force(MOTOR, 20.0, 0.0, 0.18)
    assert f == 0.0


def test_brake_adhesion_limit_rain():
    """v=20, brake=1.0, rain(0.10): 黏着限制 58860 → -58860 N"""
    f = calc_brake_force(MOTOR, 20.0, 1.0, 0.10)
    adhesion_limit = 0.10 * 60000 * 9.81  # 58860
    assert abs(f - (-adhesion_limit)) < 1.0


def test_brake_trailer():
    """拖车制动也能产生制动力。"""
    f = calc_brake_force(TRAILER, 20.0, 1.0, 0.18)
    # max=65000+(80000-65000)*1=80000, mag=80000, adhesion_limit=0.18*46000*9.81=81226.8
    # 80000 ≤ 81226.8, so f = -80000
    assert abs(f - (-80000.0)) < 1.0


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
    print("All forces tests passed!")
