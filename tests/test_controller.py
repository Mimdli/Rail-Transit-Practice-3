"""VehicleController 单元测试 — 阶段 9

测试范围:
    1. 初始化和状态重置
    2. 控制指令生效
    3. 4M2T 完整场景（启动→加速→巡航→制动→停车）
    4. 1M4T 完整场景（动力集中）
    5. 联锁约束生效
    6. 历史记录正确性
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.consist import CONSIST_4M2T, CONSIST_1M4T
from src.common.track_position import MockTrackQuery
from src.vehicle.vehicle_controller import (
    VehicleController, MockInterlock,
)
from src.vehicle.force_report import ForceReport

# 使用与 Pipeline 测试相同的 MockEnvironment
from tests.test_dynamics_pipeline import MockEnvironment, WeatherType


# ═══════════════════════════════════════════════════════════════
# 初始化测试
# ═══════════════════════════════════════════════════════════════

def test_controller_initialization():
    """控制器初始化：默认参数正确。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    assert len(ctrl.states) == 6
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 0.0
    assert ctrl.sim_time == 0.0
    assert ctrl.head_speed == 0.0
    assert ctrl.is_stopped


def test_controller_reset():
    """reset_states 清除状态。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(1.0)
    for _ in range(10):
        ctrl.step(0.033)

    assert ctrl.head_speed > 0.0
    assert len(ctrl.history) == 10

    ctrl.reset_states(start_offset=100.0)
    assert ctrl.head_speed == 0.0
    assert len(ctrl.history) == 0
    assert ctrl.sim_time == 0.0


# ═══════════════════════════════════════════════════════════════
# 控制指令测试
# ═══════════════════════════════════════════════════════════════

def test_set_throttle():
    """set_throttle 限制在 [0, 1] 范围。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(0.5)
    assert ctrl.throttle == 0.5

    ctrl.set_throttle(2.0)
    assert ctrl.throttle == 1.0  # clamped

    ctrl.set_throttle(-1.0)
    assert ctrl.throttle == 0.0  # clamped


def test_set_brake():
    """set_brake 限制在 [0, 1] 范围。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_brake(0.8)
    assert ctrl.brake_level == 0.8

    ctrl.set_brake(1.5)
    assert ctrl.brake_level == 1.0


def test_emergency_brake():
    """emergency_brake 将 throttle 归零、brake 设为最大。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(1.0)
    ctrl.emergency_brake()
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 1.0


def test_coast():
    """coast 将 throttle 和 brake 均归零。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(0.8)
    ctrl.set_brake(0.3)
    ctrl.coast()
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 0.0


# ═══════════════════════════════════════════════════════════════
# 联锁约束测试
# ═══════════════════════════════════════════════════════════════

def test_interlock_traction_not_permitted():
    """联锁禁止牵引时，throttle 被强制为 0。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    interlock = MockInterlock(traction_permitted=False)
    ctrl = VehicleController(CONSIST_4M2T, track, env, interlock=interlock)

    ctrl.set_throttle(1.0)
    report = ctrl.step(0.033)

    # 牵引力应为 0（因联锁禁止）
    for car in report.cars:
        if car.car_index == 0:  # 动车应无牵引力
            assert car.tractive_force == 0.0


def test_interlock_emergency_brake_required():
    """联锁要求紧急制动时，即使设置了 throttle，制动也应生效。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    # 先正常加速几步
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    ctrl.set_throttle(1.0)
    for _ in range(30):  # ~1s
        ctrl.step(0.033)

    # 现在设置紧急制动联锁
    ctrl.interlock.emergency_brake_required = True
    ctrl.set_throttle(1.0)  # 即使设置牵引
    ctrl.set_brake(0.0)     # 即使设置无制动
    report = ctrl.step(0.033)

    # 应有制动力（紧急制动覆盖了 throttle 和 brake 设置）
    has_brake = any(car.brake_force < 0 for car in report.cars)
    assert has_brake, "联锁要求紧急制动但未施加制动力"


# ═══════════════════════════════════════════════════════════════
# 完整场景测试
# ═══════════════════════════════════════════════════════════════

def test_4M2T_full_scenario():
    """4M2T 编组完整场景：启动→加速→惰行→制动→停车。

    验收: 列车能正常启动和停止，无数值爆炸。
    """
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    # Phase 1: 加速 5 秒
    ctrl.set_throttle(1.0)
    for _ in range(150):  # 150 * 0.033 ≈ 5s
        ctrl.step(0.033)

    speed_after_accel = ctrl.head_speed
    assert speed_after_accel > 1.0, f"加速后速度应 > 1 m/s, 实际 {speed_after_accel:.2f}"

    # Phase 2: 惰行 2 秒
    ctrl.coast()
    for _ in range(60):  # 60 * 0.033 ≈ 2s
        ctrl.step(0.033)

    speed_after_coast = ctrl.head_speed
    # 惰行时速度应略有下降或持平（阻力作用）
    assert speed_after_coast <= speed_after_accel + 0.1

    # Phase 3: 制动至停车
    ctrl.set_brake(1.0)
    for _ in range(300):  # 最多 10 秒制动
        ctrl.step(0.033)
        if ctrl.is_stopped:
            break

    assert ctrl.is_stopped, "列车应已停止"
    assert ctrl.sim_time > 0.0


def test_1M4T_full_scenario():
    """1M4T 动力集中编组：单台动车牵引全部拖车。

    验证: 所有拖车能被动车通过车钩拉动。
    """
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_1M4T, track, env)

    assert len(ctrl.states) == 5
    assert ctrl.consist.motor_count == 1
    assert ctrl.consist.trailer_count == 4

    # 加速
    ctrl.set_throttle(1.0)
    for _ in range(100):  # ~3.3s
        ctrl.step(0.033)

    # 所有车都应有正速度（包括拖车）
    for i, s in enumerate(ctrl.states):
        assert s.velocity >= 0.0, f"Car {i} ({'M' if ctrl.consist.is_motor(i) else 'T'}): 速度为负"

    # 动车速度应 > 0（牵引生效）
    assert ctrl.states[0].velocity > 0.0, "动车应有正速度"


def test_throttle_effect():
    """throttle=1.0 比 throttle=0.0 产生更高速度。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)

    # 全牵引
    ctrl_full = VehicleController(CONSIST_4M2T, track, env)
    ctrl_full.set_throttle(1.0)
    for _ in range(60):  # ~2s
        ctrl_full.step(0.033)

    # 无牵引（需要新的控制器实例）
    track2 = MockTrackQuery()
    env2 = MockEnvironment(WeatherType.DRY)
    ctrl_none = VehicleController(CONSIST_4M2T, track2, env2)
    ctrl_none.set_throttle(0.0)
    for _ in range(60):
        ctrl_none.step(0.033)

    assert ctrl_full.head_speed > ctrl_none.head_speed, (
        f"牵引应产生更高速度: full={ctrl_full.head_speed:.3f}, none={ctrl_none.head_speed:.3f}"
    )


# ═══════════════════════════════════════════════════════════════
# 历史记录测试
# ═══════════════════════════════════════════════════════════════

def test_history_recording():
    """每步产生一条 ForceReport 记录。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    for _ in range(5):
        ctrl.step(0.033)

    assert len(ctrl.history) == 5
    for i, report in enumerate(ctrl.history):
        assert isinstance(report, ForceReport)
        assert report.step == i + 1


def test_last_report():
    """last_report 返回最近一步的报告。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    assert ctrl.last_report is None

    ctrl.step(0.033)
    assert ctrl.last_report is not None
    assert ctrl.last_report.step == 1


def test_head_speed_kmh():
    """head_speed_kmh 正确转换。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(1.0)
    for _ in range(30):
        ctrl.step(0.033)

    assert abs(ctrl.head_speed_kmh - ctrl.head_speed * 3.6) < 1e-9


if __name__ == "__main__":
    # 初始化
    test_controller_initialization()
    test_controller_reset()
    # 控制指令
    test_set_throttle()
    test_set_brake()
    test_emergency_brake()
    test_coast()
    # 联锁
    test_interlock_traction_not_permitted()
    test_interlock_emergency_brake_required()
    # 完整场景
    test_4M2T_full_scenario()
    test_1M4T_full_scenario()
    test_throttle_effect()
    # 历史
    test_history_recording()
    test_last_report()
    test_head_speed_kmh()
    print("All controller tests passed!")
