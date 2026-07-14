"""AutoDriveController 单元测试

测试物理制动曲线精确停车 + 停站状态机：
    1. 巡航段（距离 > d_brake）→ 按限速巡航
    2. 制动段（距离 ≤ d_brake）→ sqrt(2*a*d) 物理制动曲线
    3. 停站段 → 自动开门 → 停站计时 → 关门发车
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.consist import CONSIST_4M2T
from src.common.track_position import TrackPosition, MockTrackQuery
from src.vehicle.vehicle_controller import VehicleController
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.enums import RunningMode, ControlLevel
from src.vehicle.environment import MockEnvironment, WeatherType


# ═══════════════════════════════════════════════════════════════
# 初始化测试
# ═══════════════════════════════════════════════════════════════

def test_auto_drive_initialization():
    """默认参数正确设置。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    assert auto.cruise_speed_factor == 0.9
    assert auto.approach_speed == 5.0
    assert auto.dwell_time == 20.0
    assert auto.target_position is None
    assert auto.station_phase.name == "CRUISING"


def test_auto_drive_custom_params():
    """自定义参数正确设置。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(
        ctrl,
        cruise_speed_factor=0.8,
        approach_speed=3.0,
        dwell_time=30.0,
    )
    assert auto.cruise_speed_factor == 0.8
    assert auto.approach_speed == 3.0
    assert auto.dwell_time == 30.0


# ═══════════════════════════════════════════════════════════════
# 目标设置测试
# ═══════════════════════════════════════════════════════════════

def test_set_target():
    """set_target 存储目标位置并计算距离。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    target = TrackPosition(segment_id=1, offset=500.0)
    auto.set_target(target)

    assert auto.target_position is not None
    assert auto.target_position.segment_id == 1
    assert auto.target_position.offset == 500.0
    # 头车从 offset=0 开始（根据默认 reset_states），距离 = 500
    dist = auto.distance_to_target
    assert dist is not None
    assert dist > 0, f"目标在前方，距离应为正，实际 {dist}"


def test_step_no_target_is_safe():
    """未设置目标时 step() 应是安全的 no-op。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    # 不设置目标直接 step，不应抛异常
    auto.step()
    # 默认 throttle/brake 保持不变
    assert ctrl.throttle == 0.0
    assert ctrl.brake_level == 0.0


# ═══════════════════════════════════════════════════════════════
# 控制逻辑测试
# ═══════════════════════════════════════════════════════════════

def test_cruise_regime_sets_traction():
    """目标远在前方时应进入巡航段 (MEDIUM_TRACTION)。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    # 设置远处的目标（> stop_distance = 20m）
    auto.set_target(TrackPosition(segment_id=1, offset=500.0))
    auto.step()

    # 头车速度 = 0，限速 22.22，cruise_speed = 20.0
    # speed(0) < cruise_speed(20.0) → MEDIUM_TRACTION
    assert ctrl.throttle > 0, "巡航段应对低速列车施加牵引"
    assert ctrl.brake_level == 0.0


def test_deceleration_regime_brakes_when_overspeed():
    """在比例减速段超速时应施加制动。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl, approach_speed=1.0)

    # 先加速到较高速度（多跑一些步）
    ctrl.set_throttle(1.0)
    for _ in range(200):  # ~6.6s
        ctrl.step(0.033)

    speed_before = ctrl.head_speed
    assert speed_before > 2.0, f"加速后速度应 > 2 m/s, 实际 {speed_before:.2f}"

    # 设置近距离目标（在 stop_distance 内）
    # target_speed = 1.0 * (distance / 50.0)
    # 设 distance ≈ 10m → target_speed = 0.2
    # 当前速度 > 2.0 远超 target_speed + 0.5 → 应制动
    current_offset = ctrl.states[0].position.offset
    auto.set_target(TrackPosition(segment_id=1, offset=current_offset + 10.0))
    auto.step()

    assert ctrl.brake_level > 0, f"超速应制动, speed={speed_before:.2f}, brake={ctrl.brake_level}"


def test_emergency_brake_regime():
    """距离 ≤ 3m 且仍在移动时应用渐进制动（常用制动上限 0.7，
    非紧急制动 1.0——正常运营不使用紧急制动对标停车）。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    # 先让列车获得一些速度
    ctrl.set_throttle(1.0)
    for _ in range(50):  # ~1.65s 加速
        ctrl.step(0.033)

    speed_before = ctrl.head_speed
    assert speed_before > 0.5, f"列车应有一定速度，实际 {speed_before:.2f}"

    # 设置极近距离目标（0.3m 前方），列车仍在移动中
    # 新逻辑：0.3m 触发蠕行阶段，施加渐进制动（上限 0.7），非紧急制动
    head_offset = ctrl.states[0].position.offset
    auto.set_target(TrackPosition(segment_id=1, offset=head_offset + 0.3))
    auto.step()

    assert ctrl.brake_level >= 0.4, f"近距离应施加制动, brake={ctrl.brake_level}"
    assert ctrl.brake_level <= 0.7, (
        f"正常对标不应使用紧急制动(1.0), brake={ctrl.brake_level}"
    )
    assert ctrl.throttle == 0.0


def test_auto_drive_sets_running_mode():
    """step() 不强制修改运行模式——由调用者负责设置。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    assert ctrl.running_mode == RunningMode.MANUAL  # 默认手动

    # auto_drive 不自动切换模式，调用者需显式设置
    ctrl.set_running_mode(RunningMode.AUTOMATIC)
    auto.set_target(TrackPosition(segment_id=1, offset=500.0))
    auto.step()
    # 模式保持不变（由调用者管理）
    assert ctrl.running_mode == RunningMode.AUTOMATIC


# ═══════════════════════════════════════════════════════════════
# 端到端测试
# ═══════════════════════════════════════════════════════════════

def test_auto_drive_full_stop():
    """端到端：设置目标 → 自动运行至停车 → 验证位置在目标附近。

    注意: PT1 滤波导致初始响应延迟，需运行足够步数后再检查 is_stopped。
    """
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)

    # 头车从 offset=0 开始
    target_offset = 200.0  # 200m 前方停车
    auto = AutoDriveController(ctrl)
    auto.set_target(TrackPosition(segment_id=1, offset=target_offset))
    ctrl.set_running_mode(RunningMode.AUTOMATIC)  # 调用者负责设置模式

    max_steps = 2000  # 最多 ~66 秒
    min_steps = 30    # 至少运行 30 步 (~1s) 以克服 PT1 初始延迟
    step = 0
    for step in range(max_steps):
        auto.step()
        ctrl.step(0.033)
        if step >= min_steps and auto.station_phase.name == "DWELL":
            break

    assert step < max_steps - 1, f"列车应在目标附近停车, 运行了{max_steps}步未停, speed={ctrl.head_speed:.2f}"

    # 头车应前进了相当距离（验证自动控制循环正常运行）
    head_abs = track.to_absolute(ctrl.states[0].position)
    assert head_abs > 50.0, f"列车应前进了, 实际 head_abs={head_abs:.1f}m"
    assert abs(head_abs - target_offset) <= auto.STOP_POSITION_TOLERANCE

    # 所有车应已停止
    for s in ctrl.states:
        assert s.velocity < 0.1, f"有车厢未停止: v={s.velocity:.3f}"


def test_auto_drive_restarts_creep_when_stopped_short_of_target():
    """提前停在目标前方时应重新低速牵引，而不是误判到站。"""
    track = MockTrackQuery()
    ctrl = VehicleController(
        CONSIST_4M2T, track, MockEnvironment(WeatherType.DRY))
    auto = AutoDriveController(ctrl)
    ctrl.reset_to(TrackPosition(segment_id=1, offset=199.0))
    auto.set_target(TrackPosition(segment_id=1, offset=200.0))

    auto.step()

    assert auto.station_phase.name == "APPROACHING"
    assert ctrl.throttle == 0.3
    assert ctrl.brake_level == 0.0

    # 完整编组可能在停车点前以极低速卡滞，此时仍应保持起步牵引。
    for state in ctrl.states:
        state.velocity = 0.011
    auto.step()
    assert ctrl.throttle == 0.3
    assert ctrl.brake_level == 0.0


def test_distance_to_target_updates():
    """distance_to_target 随仿真推进而减小。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    auto.set_target(TrackPosition(segment_id=1, offset=500.0))
    dist_before = auto.distance_to_target
    assert dist_before is not None and dist_before > 0

    # 运行几步
    ctrl.set_throttle(1.0)
    for _ in range(30):
        ctrl.step(0.033)

    dist_after = auto.distance_to_target
    assert dist_after is not None
    assert dist_after < dist_before, "距离应随时间减小"


# ═══════════════════════════════════════════════════════════════
# 状态查询测试
# ═══════════════════════════════════════════════════════════════

def test_is_stopped_delegates():
    """is_stopped 应委托给 VehicleController。"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY)
    ctrl = VehicleController(CONSIST_4M2T, track, env)
    auto = AutoDriveController(ctrl)

    assert auto.is_stopped is True
    assert ctrl.is_stopped is True


if __name__ == "__main__":
    # 初始化
    test_auto_drive_initialization()
    test_auto_drive_custom_params()
    # 目标设置
    test_set_target()
    test_step_no_target_is_safe()
    # 三阶段控制
    test_cruise_regime_sets_traction()
    test_deceleration_regime_brakes_when_overspeed()
    test_emergency_brake_regime()
    test_auto_drive_sets_running_mode()
    # 端到端
    test_auto_drive_full_stop()
    test_distance_to_target_updates()
    # 状态查询
    test_is_stopped_delegates()
    print("All auto_drive tests passed!")
