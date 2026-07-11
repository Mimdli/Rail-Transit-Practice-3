"""TractionBrakeController — 牵引/制动执行器

从 VehicleController 中提取的纯粹牵引/制动控制逻辑。

职责:
    1. 管理 throttle / brake_level / target_speed 控制指令
    2. PT1 滤波器协调（与动力学管线配合）
    3. 离散控制级位映射（ControlLevel → throttle/brake）
    4. 联锁约束执行（门-牵引联锁、紧急制动覆盖）
    5. 提供高层控制指令（发车、停车、紧急制动）

与 VehicleController 的关系:
    - VehicleController 持有 TractionBrakeController 实例
    - step() 时从 TractionBrakeController 读取有效 throttle/brake
    - 通过代理属性保持向后兼容
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING
from src.vehicle.enums import ControlLevel, VehicleState, RunningMode, CONTROL_LEVEL_MAP

if TYPE_CHECKING:
    from src.vehicle.dynamics_pipeline import PerCarDynamicsPipeline


# ═══════════════════════════════════════════════════════════════
# 联锁约束
# ═══════════════════════════════════════════════════════════════

@dataclass
class TractionInterlock:
    """牵引/制动联锁约束。

    集成时可替换为真实联锁模块。
    """
    traction_permitted: bool = True
    emergency_brake_required: bool = False
    door_open: bool = False  # 任何车门开启时为 True


# ═══════════════════════════════════════════════════════════════
# TractionBrakeController
# ═══════════════════════════════════════════════════════════════

class TractionBrakeController:
    """牵引/制动执行器 —— 将驾驶指令转换为物理力控制信号。

    所有牵引力和制动力的控制都通过此对象发出。
    它不执行物理仿真，只管理控制信号的产生和滤波。

    Usage:
        tbc = TractionBrakeController(pipeline)
        tbc.command_start()         # 释放制动，准备发车
        tbc.set_throttle(0.8)       # 设牵引力
        # VehicleController.step() 中使用:
        throttle, brake = tbc.get_effective_command(interlock)
    """

    def __init__(self, pipeline: "PerCarDynamicsPipeline | None" = None):
        """
        Args:
            pipeline: 动力学管线引用，用于滤波器重置协调。
                      创建后可稍后通过 set_pipeline() 设置。
        """
        self._pipeline = pipeline

        # ── 控制指令 ─────────────────────────────────────────────
        self.throttle: float = 0.0       # 0.0 ~ 1.0
        self.brake_level: float = 0.0    # 0.0 ~ 1.0
        self.target_speed: float = 0.0   # m/s（目标速度，0 表示不限速）

        # ── 驾驶模式门禁 ─────────────────────────────────────────
        self._driving_mode: RunningMode = RunningMode.MANUAL

        # ── 车辆状态（由 VehicleController 同步） ────────────────
        self.vehicle_state: VehicleState = VehicleState.INIT

    # ── Pipeline 绑定 ───────────────────────────────────────────

    def set_pipeline(self, pipeline: "PerCarDynamicsPipeline"):
        """设置/更换动力学管线引用。"""
        self._pipeline = pipeline

    @property
    def pipeline(self):
        return self._pipeline

    # ── 驾驶模式管理 ────────────────────────────────────────────

    def set_driving_mode(self, mode: RunningMode):
        """设置驾驶模式。

        由 VehicleController.set_running_mode() 调用以保持同步。

        Args:
            mode: RunningMode.MANUAL 或 RunningMode.AUTOMATIC。
        """
        self._driving_mode = mode

    @property
    def driving_mode(self) -> RunningMode:
        """获取当前驾驶模式。"""
        return self._driving_mode

    # ── 连续控制指令 ────────────────────────────────────────────

    def set_throttle(self, level: float, *, bypass_mode_check: bool = False):
        """设置牵引手柄位。

        在 AUTOMATIC 模式下，非旁路调用（如 UI 按钮）将被忽略，
        仅 AutoDriveController 可通过 bypass_mode_check=True 设置。

        Args:
            level: 牵引力比例 (0.0 ~ 1.0)。
            bypass_mode_check: 内部旁路，供 AutoDriveController 使用。
        """
        if not bypass_mode_check and self._driving_mode != RunningMode.MANUAL:
            return
        self.throttle = max(0.0, min(1.0, level))
        # 施加牵引时立即释放制动滤波器，避免 PT1 衰减延迟
        if level > 0 and self._pipeline is not None:
            self._pipeline.reset_filters(throttle=False, brake=True)

    def set_brake(self, level: float, *, bypass_mode_check: bool = False):
        """设置制动级别。

        在 AUTOMATIC 模式下，非旁路调用（如 UI 按钮）将被忽略，
        仅 AutoDriveController 可通过 bypass_mode_check=True 设置。

        Args:
            level: 制动级别 (0.0 ~ 1.0，1.0 = 紧急制动)。
            bypass_mode_check: 内部旁路，供 AutoDriveController 使用。
        """
        if not bypass_mode_check and self._driving_mode != RunningMode.MANUAL:
            return
        self.brake_level = max(0.0, min(1.0, level))
        if self._pipeline is not None:
            # 制动归零时立即重置滤波器，模拟制动缸快速排风
            if level == 0.0:
                self._pipeline.reset_filters(throttle=False, brake=True)
            # 施加制动时重置牵引滤波器
            if level > 0:
                self._pipeline.reset_filters(throttle=True, brake=False)

    def set_target_speed(self, speed: float):
        """设置目标速度 (m/s)。控制器需自行实现调速逻辑。"""
        self.target_speed = max(0.0, speed)

    # ── 高层控制指令 ────────────────────────────────────────────

    def command_start(self) -> bool:
        """发车指令：释放制动，准备施加牵引。

        仅在 STOPPED 状态下有效。调用者应在调用此方法后
        设置具体牵引级位（set_throttle 或 apply_control_level）。

        Returns:
            True 如果指令被接受。
        """
        if self.vehicle_state not in (VehicleState.STOPPED, VehicleState.INIT):
            return False
        self.set_brake(0.0, bypass_mode_check=True)
        self.vehicle_state = VehicleState.STARTING
        return True

    def command_stop(self, brake_level: float = 0.4):
        """停车指令：施加常用制动。

        Args:
            brake_level: 制动级别，默认 0.4（常用制动）。
        """
        self.set_throttle(0.0, bypass_mode_check=True)
        self.set_brake(brake_level, bypass_mode_check=True)
        if self._pipeline is not None:
            self._pipeline.reset_filters(throttle=True, brake=True)
        self.vehicle_state = VehicleState.BRAKING

    def command_emergency(self):
        """紧急制动指令：最大制动力，立即切断牵引。"""
        self.throttle = 0.0
        self.brake_level = 1.0
        if self._pipeline is not None:
            self._pipeline.reset_filters(throttle=True, brake=False)
        self.vehicle_state = VehicleState.EMERGENCY

    def command_coast(self):
        """惰行指令：无牵引无制动。"""
        self.throttle = 0.0
        self.brake_level = 0.0
        if self._pipeline is not None:
            self._pipeline.reset_filters(throttle=True, brake=True)
        self.vehicle_state = VehicleState.COASTING

    # ── 离散控制级位 ────────────────────────────────────────────

    def apply_control_level(self, level: ControlLevel):
        """将离散司控器级位映射为连续 throttle/brake 并应用。

        不旁路模式门禁 —— 调用者（VehicleController.apply_control_level()）
        已在上层检查过 running_mode == MANUAL，此处由 TractionBrakeController
        的模式门禁提供第二层防护，防止直接调用本方法绕过上层检查。

        Args:
            level: 司控器级位（ControlLevel 枚举值）。
        """
        if level not in CONTROL_LEVEL_MAP:
            return
        throttle, brake = CONTROL_LEVEL_MAP[level]
        self.set_throttle(throttle)
        self.set_brake(brake)

    # ── 联锁评估 ────────────────────────────────────────────────

    def get_effective_command(self,
                              interlock: "TractionInterlock | None" = None
                              ) -> tuple[float, float]:
        """根据联锁约束计算本次步进中应使用的有效 throttle/brake。

        此方法由 VehicleController.step() 调用，在推进物理仿真之前
        评估联锁条件并返回修正后的控制值。

        Args:
            interlock: 联锁约束（可选）。为 None 时不检查联锁。

        Returns:
            (effective_throttle, effective_brake) 元组。
        """
        throttle = self.throttle
        brake = self.brake_level

        if interlock is None:
            return throttle, brake

        # 门-牵引联锁：任何车门开启时禁止牵引
        if interlock.door_open:
            throttle = 0.0

        # 紧急制动优先级最高
        if interlock.emergency_brake_required:
            return 0.0, 1.0

        # 牵引未授权
        if not interlock.traction_permitted:
            throttle = 0.0

        return throttle, brake

    # ── 状态同步 ────────────────────────────────────────────────

    def update_vehicle_state(self, is_stopped: bool, head_speed: float):
        """根据物理状态自动更新车辆运行状态（非命令驱动的转换）。

        由 VehicleController.step() 在每步结束后调用。

        Args:
            is_stopped: 所有车厢是否已停止。
            head_speed: 头车速度 (m/s)。
        """
        state = self.vehicle_state

        # EMERGENCY → STOPPED（确认完全停止）
        if state == VehicleState.EMERGENCY and is_stopped:
            self.vehicle_state = VehicleState.STOPPED
            return

        # BRAKING → STOPPED
        if state == VehicleState.BRAKING and is_stopped:
            self.vehicle_state = VehicleState.STOPPED
            return

        # STARTING → MOVING（速度 > 阈值）
        if state == VehicleState.STARTING and head_speed > 0.01:
            self.vehicle_state = VehicleState.MOVING
            return

        # MOVING → COASTING（无牵引无制动）
        if state in (VehicleState.MOVING, VehicleState.STARTING):
            if self.throttle == 0.0 and self.brake_level == 0.0 and head_speed > 0.01:
                self.vehicle_state = VehicleState.COASTING
                return

        # COASTING → MOVING（重新施加牵引）
        if state == VehicleState.COASTING and self.throttle > 0:
            self.vehicle_state = VehicleState.MOVING
            return

        # COASTING → BRAKING（施加制动）
        if state == VehicleState.COASTING and self.brake_level > 0:
            self.vehicle_state = VehicleState.BRAKING
            return

        # MOVING/COASTING → BRAKING（施加制动且仍在移动）
        if state in (VehicleState.MOVING, VehicleState.COASTING) and self.brake_level > 0:
            self.vehicle_state = VehicleState.BRAKING
            return

        # MOVING/COASTING → STOPPED（自然停止，无制动命令）
        if state in (VehicleState.MOVING, VehicleState.COASTING) and is_stopped:
            # 未施加制动但列车停了（如阻力自然停车），保持当前 throttle/brake
            # 施加保持制动
            self.vehicle_state = VehicleState.STOPPED
            return

    def reset(self):
        """重置控制指令和车辆状态。"""
        self.throttle = 0.0
        self.brake_level = 0.0
        self.target_speed = 0.0
        self.vehicle_state = VehicleState.INIT
        if self._pipeline is not None:
            self._pipeline.reset_filters(throttle=True, brake=True)
