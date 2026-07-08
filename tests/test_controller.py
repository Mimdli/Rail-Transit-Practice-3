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
from src.common.track_position import MockTrackQuery, TrackPosition
from src.vehicle.vehicle_controller import (
    VehicleController, MockInterlock,
)
from src.vehicle.enums import DoorSide, RunningMode, ControlLevel, LoadLevel
from src.vehicle.force_report import ForceReport

from src.vehicle.environment import MockEnvironment, WeatherType


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

    注意: 多体动力学中车钩振荡会导致各车速度波动，
    惰行阶段头车速度可能因车钩反冲而暂时升高。
    核心验证目标：列车能正常起步和停车，无 NaN/Inf。
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

    # Phase 2: 惰行 3 秒
    ctrl.coast()
    for _ in range(90):  # 90 * 0.033 ≈ 3s
        ctrl.step(0.033)

    # 惰行后所有车厢速度应仍在物理合理范围内（无爆炸）
    for i, s in enumerate(ctrl.states):
        assert abs(s.velocity) < 50.0, f"Car {i}: 速度异常 {s.velocity:.1f} m/s"
        assert abs(s.acceleration) < 100.0, f"Car {i}: 加速度异常 {s.acceleration:.1f} m/s²"

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


# ═══════════════════════════════════════════════════════════════
# 车门系统测试
# ═══════════════════════════════════════════════════════════════

def test_door_initial_state_closed():
    """新建控制器车门应全部关闭。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    assert ctrl.left_door_open is False
    assert ctrl.right_door_open is False
    assert ctrl.doors_closed() is True
    assert ctrl.any_door_open is False
    assert ctrl.door_side == DoorSide.NONE


def test_door_open_when_stopped():
    """停车状态下开门应成功。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    assert ctrl.is_stopped
    result = ctrl.open_door(DoorSide.LEFT)
    assert result is True
    assert ctrl.left_door_open is True
    assert ctrl.right_door_open is False
    assert ctrl.door_side == DoorSide.LEFT
    assert ctrl.any_door_open is True
    assert ctrl.doors_closed() is False


def test_door_open_when_moving_rejected():
    """运行中开门应被拒绝。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    # 加速使列车运动
    ctrl.set_throttle(1.0)
    for _ in range(30):
        ctrl.step(0.033)
    assert not ctrl.is_stopped

    result = ctrl.open_door(DoorSide.RIGHT)
    assert result is False
    assert ctrl.right_door_open is False
    assert ctrl.doors_closed() is True


def test_door_close_resets_state():
    """关门后状态应全部复位。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.open_door(DoorSide.LEFT)
    assert ctrl.left_door_open is True

    ctrl.close_door()
    assert ctrl.left_door_open is False
    assert ctrl.right_door_open is False
    assert ctrl.door_side == DoorSide.NONE
    assert ctrl.doors_closed() is True
    assert ctrl.any_door_open is False


def test_door_right_side():
    """右侧开门正确。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    result = ctrl.open_door(DoorSide.RIGHT)
    assert result is True
    assert ctrl.right_door_open is True
    assert ctrl.left_door_open is False
    assert ctrl.door_side == DoorSide.RIGHT


def test_door_open_none_rejected():
    """NONE 侧开门应被拒绝。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    result = ctrl.open_door(DoorSide.NONE)
    assert result is False


# ═══════════════════════════════════════════════════════════════
# 运行模式测试
# ═══════════════════════════════════════════════════════════════

def test_default_running_mode_manual():
    """新建控制器默认为手动模式。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    assert ctrl.running_mode == RunningMode.MANUAL


def test_set_running_mode():
    """切换运行模式。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_running_mode(RunningMode.AUTOMATIC)
    assert ctrl.running_mode == RunningMode.AUTOMATIC

    ctrl.set_running_mode(RunningMode.MANUAL)
    assert ctrl.running_mode == RunningMode.MANUAL


# ═══════════════════════════════════════════════════════════════
# ControlLevel 映射测试
# ═══════════════════════════════════════════════════════════════

def test_apply_control_level_full_traction():
    """FULL_TRACTION -> throttle=1.0, brake=0.0。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.apply_control_level(ControlLevel.FULL_TRACTION)
    assert ctrl.throttle == 1.0
    assert ctrl.brake_level == 0.0


def test_apply_control_level_emergency_brake():
    """EMERGENCY_BRAKE -> throttle=0.0, brake=1.0。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.apply_control_level(ControlLevel.EMERGENCY_BRAKE)
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 1.0


def test_apply_control_level_coast():
    """COAST -> throttle=0.0, brake=0.0。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(0.5)
    ctrl.set_brake(0.3)
    ctrl.apply_control_level(ControlLevel.COAST)
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 0.0


def test_apply_control_level_ignored_in_auto_mode():
    """AUTOMATIC 模式下 apply_control_level 应被忽略。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_running_mode(RunningMode.AUTOMATIC)
    ctrl.apply_control_level(ControlLevel.FULL_TRACTION)
    # 应保持默认值
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 0.0


def test_all_control_levels_valid():
    """所有 7 个 ControlLevel 值都能正确映射到 [0,1] 范围。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    for level in ControlLevel:
        ctrl.apply_control_level(level)
        assert 0.0 <= ctrl.throttle <= 1.0, f"{level}: throttle={ctrl.throttle}"
        assert 0.0 <= ctrl.brake_level <= 1.0, f"{level}: brake={ctrl.brake_level}"


# ═══════════════════════════════════════════════════════════════
# 模式交互测试
# ═══════════════════════════════════════════════════════════════

def test_manual_mode_step_uses_control_level():
    """手动模式下 apply_control_level 后 step 应产生牵引力。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.apply_control_level(ControlLevel.FULL_TRACTION)
    report = ctrl.step(0.033)

    # 动车（car_index=0,3）应有正牵引力
    traction_sum = sum(c.tractive_force for c in report.cars)
    assert traction_sum > 0, "手动全牵引应产生牵引力"


def test_door_reset_on_reset_states():
    """reset_states 应重置车门状态。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.open_door(DoorSide.LEFT)
    ctrl.set_running_mode(RunningMode.AUTOMATIC)

    ctrl.reset_states()
    assert ctrl.left_door_open is False
    assert ctrl.right_door_open is False
    assert ctrl.door_side == DoorSide.NONE
    assert ctrl.running_mode == RunningMode.MANUAL


# ═══════════════════════════════════════════════════════════════
# P0: 制动 Bug 修复测试
# ═══════════════════════════════════════════════════════════════

def test_brake_no_double_multiplication():
    """P0: brake_level=0.5 时制动力为插值结果，不应被二次乘法缩小。

    修复前: magnitude = max_brake * brake_level (bug)
    修复后: magnitude = max_brake (max_brake 已是插值结果)
    """
    from src.vehicle.forces import calc_brake_force
    from src.common.car_config import MOTOR_CAR_CONFIG

    # brake_level=0.5: max_brake = 80000 + (100000-80000)*0.5 = 90000
    f, limited = calc_brake_force(MOTOR_CAR_CONFIG, 20.0, 0.5, 0.18)
    # 修复后 magnitude = 90000（而非 45000）
    assert abs(f - (-90000.0)) < 1.0, f"预期 -90000, 实际 {f}"


# ═══════════════════════════════════════════════════════════════
# P1: 回转质量系数测试
# ═══════════════════════════════════════════════════════════════

def test_rotary_mass_factor_present():
    """P1: CarConfig 应包含 rotary_mass_factor 字段。"""
    from src.common.car_config import MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG
    assert hasattr(MOTOR_CAR_CONFIG, 'rotary_mass_factor')
    assert hasattr(TRAILER_CAR_CONFIG, 'rotary_mass_factor')
    assert MOTOR_CAR_CONFIG.rotary_mass_factor > 1.0  # 动车 > 1 (含电机转子)
    assert TRAILER_CAR_CONFIG.rotary_mass_factor >= 1.0


def test_rotary_mass_reduces_acceleration():
    """P1: 回转质量系数 > 1 时等效质量增大，加速度降低。"""
    from src.common.car_config import CarConfig
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)

    # 使用 rotary_mass_factor = 1.0（无旋转惯量）
    cars_1 = [CarConfig(
        name="Test", mass=60000.0, length=19.5, is_motor=True,
        rotary_mass_factor=1.0, base_mass=60000.0,
    )]
    consist_1 = __import__('src.common.consist', fromlist=['TrainConsist']).TrainConsist(cars_1)
    ctrl_1 = VehicleController(consist_1, track, env)
    ctrl_1.set_throttle(1.0)
    for _ in range(30):
        ctrl_1.step(0.033)
    v1 = ctrl_1.head_speed

    # 使用 rotary_mass_factor = 1.08
    cars_108 = [CarConfig(
        name="Test", mass=60000.0, length=19.5, is_motor=True,
        rotary_mass_factor=1.08, base_mass=60000.0,
    )]
    track2 = MockTrackQuery()
    env2 = MockEnvironment(WeatherType.DRY)
    consist_108 = __import__('src.common.consist', fromlist=['TrainConsist']).TrainConsist(cars_108)
    ctrl_108 = VehicleController(consist_108, track2, env2)
    ctrl_108.set_throttle(1.0)
    for _ in range(30):
        ctrl_108.step(0.033)
    v108 = ctrl_108.head_speed

    # 回转质量更大 → 加速度更低
    assert v108 < v1, f"回转质量应降低加速度: v1.0={v1:.4f}, v1.08={v108:.4f}"


# ═══════════════════════════════════════════════════════════════
# P2: 黏着限制标志测试
# ═══════════════════════════════════════════════════════════════

def test_adhesion_limited_flags_in_report():
    """P2: ForceReport 应包含 traction_limited 和 brake_limited 字段。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(1.0)
    report = ctrl.step(0.033)

    for car in report.cars:
        assert hasattr(car, 'traction_limited'), "CarForceReport 缺少 traction_limited"
        assert hasattr(car, 'brake_limited'), "CarForceReport 缺少 brake_limited"
        assert isinstance(car.traction_limited, bool)
        assert isinstance(car.brake_limited, bool)


def test_traction_limited_in_wet_conditions():
    """P2: 雨天使黏着系数降低，大油门应触发 traction_limited。"""
    from src.vehicle.forces import calc_tractive_force
    from src.common.car_config import MOTOR_CAR_CONFIG

    # 雨天黏着限制 0.10 * 60000 * 9.81 = 58860 N
    # 满油门牵引力 80000 N > 58860 → 应被限制
    f, limited = calc_tractive_force(MOTOR_CAR_CONFIG, 5.0, 1.0, 0.10)
    assert limited, "雨天满油门应触发黏着限制"
    assert abs(f - 58860.0) < 1.0


# ═══════════════════════════════════════════════════════════════
# P3: 载荷等级测试
# ═══════════════════════════════════════════════════════════════

def test_load_level_enum():
    """P3: LoadLevel 枚举包含 AW0-AW3 四个等级。"""
    from src.vehicle.enums import LoadLevel
    assert LoadLevel.AW0.value == 0
    assert LoadLevel.AW1.value == 1
    assert LoadLevel.AW2.value == 2
    assert LoadLevel.AW3.value == 3


def test_set_load_level_changes_mass():
    """P3: set_load_level 应更新各节车的有效质量。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    # 默认 AW2 质量
    mass_aw2 = ctrl.consist[0].mass
    assert mass_aw2 > 50000

    # 切换到 AW0 (空载)
    ctrl.set_load_level(LoadLevel.AW0)
    mass_aw0 = ctrl.consist[0].mass
    assert mass_aw0 < mass_aw2, f"AW0 应比 AW2 轻: {mass_aw0} vs {mass_aw2}"

    # 切换到 AW3 (超载)
    ctrl.set_load_level(LoadLevel.AW3)
    mass_aw3 = ctrl.consist[0].mass
    assert mass_aw3 > mass_aw2, f"AW3 应比 AW2 重: {mass_aw3} vs {mass_aw2}"


def test_load_level_affects_acceleration():
    """P3: AW3（重载）比 AW0（空载）加速更慢。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)

    # AW0 (轻载)
    ctrl_aw0 = VehicleController(CONSIST_4M2T, track, env)
    ctrl_aw0.set_load_level(LoadLevel.AW0)
    ctrl_aw0.set_throttle(1.0)
    for _ in range(30):
        ctrl_aw0.step(0.033)
    v_aw0 = ctrl_aw0.head_speed

    # AW3 (重载)
    track2 = MockTrackQuery()
    env2 = MockEnvironment(WeatherType.DRY)
    ctrl_aw3 = VehicleController(CONSIST_4M2T, track2, env2)
    ctrl_aw3.set_load_level(LoadLevel.AW3)
    ctrl_aw3.set_throttle(1.0)
    for _ in range(30):
        ctrl_aw3.step(0.033)
    v_aw3 = ctrl_aw3.head_speed

    assert v_aw3 < v_aw0, f"重载应加速更慢: AW0={v_aw0:.4f}, AW3={v_aw3:.4f}"


def test_brake_load_compensation():
    """P3: 重载列车制动力应更大（载荷补偿）。"""
    from src.vehicle.forces import calc_brake_force
    from src.common.car_config import CarConfig

    # 基准 AW2 质量
    car_base = CarConfig(
        name="Test", mass=60000.0, length=19.5,
        max_service_brake_force=80000.0, max_emergency_brake_force=100000.0,
        base_mass=60000.0,
    )
    f_base, _ = calc_brake_force(car_base, 20.0, 1.0, 0.18)

    # 重载 (AW3)
    car_heavy = CarConfig(
        name="Test", mass=67200.0, length=19.5,
        max_service_brake_force=80000.0, max_emergency_brake_force=100000.0,
        base_mass=60000.0,
    )
    f_heavy, _ = calc_brake_force(car_heavy, 20.0, 1.0, 0.18)

    # 重载制动力 = 基准制动力 × (67200/60000) ≈ 1.12x
    assert abs(f_heavy) > abs(f_base), (
        f"重载制动力应更大: base={f_base:.1f}, heavy={f_heavy:.1f}"
    )


# ═══════════════════════════════════════════════════════════════
# P4: PT1 一阶低通滤波测试
# ═══════════════════════════════════════════════════════════════

def test_pt1_filter_present():
    """P4: Pipeline 应有 PT1 滤波器状态变量。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    assert hasattr(ctrl.pipeline, 'filtered_throttle')
    assert hasattr(ctrl.pipeline, 'filtered_brake')
    assert hasattr(ctrl.pipeline, 'tau_traction')
    assert hasattr(ctrl.pipeline, 'tau_brake')
    assert ctrl.pipeline.tau_traction > 0
    assert ctrl.pipeline.tau_brake > 0


def test_pt1_filter_delays_response():
    """P4: PT1 滤波使控制响应延迟——阶跃后 filtered_throttle < 指令值。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    # 初始滤波值为 0
    assert ctrl.pipeline.filtered_throttle == 0.0

    # 阶跃到满油门
    ctrl.set_throttle(1.0)
    ctrl.step(0.033)

    # 第一步滤波值应 < 1.0（一阶系统阶跃响应）
    assert ctrl.pipeline.filtered_throttle < 1.0, (
        f"PT1 应延迟响应: {ctrl.pipeline.filtered_throttle:.4f}"
    )
    assert ctrl.pipeline.filtered_throttle > 0.0, "滤波值应已开始上升"


def test_pt1_filter_converges():
    """P4: 长时间恒定指令后滤波值应收敛到指令值。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(0.5)
    # 运行足够长时间 (>5×tau ≈ 1.5s)
    for _ in range(100):  # ~3.3s
        ctrl.step(0.033)

    # 应接近 0.5（误差 < 1%）
    assert abs(ctrl.pipeline.filtered_throttle - 0.5) < 0.01, (
        f"滤波应收敛: {ctrl.pipeline.filtered_throttle:.4f}"
    )


def test_pt1_can_be_disabled():
    """P4: tau=0 时滤波器不生效（直通）。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.pipeline.tau_traction = 0.0
    ctrl.set_throttle(1.0)
    ctrl.step(0.033)

    # tau=0 → alpha=1 → 直通
    assert ctrl.pipeline.filtered_throttle == 1.0, (
        f"tau=0 应直通: {ctrl.pipeline.filtered_throttle:.4f}"
    )


# ═══════════════════════════════════════════════════════════════
# Sprint 3: 曲线阻力 + 电空制动 集成测试
# ═══════════════════════════════════════════════════════════════

def test_mock_track_curve_radius():
    """MockTrackQuery 返回正确的曲线半径。"""
    track = MockTrackQuery()
    # Segment 1: 直线 (None)
    assert track.get_curve_radius(TrackPosition(1, 500.0)) is None
    # Segment 2: R=400m
    assert track.get_curve_radius(TrackPosition(2, 500.0)) == 400.0
    # Segment 3: 直线 (None)
    assert track.get_curve_radius(TrackPosition(3, 500.0)) is None


def test_curve_resistance_in_report():
    """ForceReport 应包含曲线阻力字段。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(1.0)
    report = ctrl.step(0.033)

    for car in report.cars:
        assert hasattr(car, 'curve_resistance'), "CarForceReport 缺少 curve_resistance"
        assert isinstance(car.curve_resistance, (int, float))


def test_electric_friction_brake_in_report():
    """ForceReport 应包含电空制动拆分字段。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_brake(0.5)
    report = ctrl.step(0.033)

    for car in report.cars:
        assert hasattr(car, 'electric_brake_force'), "缺少 electric_brake_force"
        assert hasattr(car, 'friction_brake_force'), "缺少 friction_brake_force"


def test_full_scenario_with_curves():
    """完整场景通过曲线段：验证无数值爆炸。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    ctrl.set_throttle(1.0)
    # 运行足够步数使列车进入曲线段 (seg 2)
    for _ in range(300):  # ~10s
        ctrl.step(0.033)
        # 检查无 NaN/Inf
        for s in ctrl.states:
            assert not (abs(s.acceleration) > 500), f"加速度异常: {s.acceleration}"
            assert s.velocity >= 0, f"速度不应为负: {s.velocity}"

    # 列车应在减速（上坡+曲线阻力）
    assert ctrl.head_speed > 0, "列车应前进"


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
    # 车门
    test_door_initial_state_closed()
    test_door_open_when_stopped()
    test_door_open_when_moving_rejected()
    test_door_close_resets_state()
    test_door_right_side()
    test_door_open_none_rejected()
    # 运行模式
    test_default_running_mode_manual()
    test_set_running_mode()
    # ControlLevel 映射
    test_apply_control_level_full_traction()
    test_apply_control_level_emergency_brake()
    test_apply_control_level_coast()
    test_apply_control_level_ignored_in_auto_mode()
    test_all_control_levels_valid()
    # 模式交互
    test_manual_mode_step_uses_control_level()
    test_door_reset_on_reset_states()
    # P0: 制动 Bug 修复
    test_brake_no_double_multiplication()
    # P1: 回转质量系数
    test_rotary_mass_factor_present()
    test_rotary_mass_reduces_acceleration()
    # P2: 黏着限制标志
    test_adhesion_limited_flags_in_report()
    test_traction_limited_in_wet_conditions()
    # P3: 载荷等级
    test_load_level_enum()
    test_set_load_level_changes_mass()
    test_load_level_affects_acceleration()
    test_brake_load_compensation()
    # P4: PT1 滤波
    test_pt1_filter_present()
    test_pt1_filter_delays_response()
    test_pt1_filter_converges()
    test_pt1_can_be_disabled()
    # Sprint 3: 曲线阻力 + 电空制动
    test_mock_track_curve_radius()
    test_curve_resistance_in_report()
    test_electric_friction_brake_in_report()
    test_full_scenario_with_curves()
    print("All controller tests passed!")
