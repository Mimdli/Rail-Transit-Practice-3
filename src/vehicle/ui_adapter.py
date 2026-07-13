"""UI 车辆适配层 — 将新多体车辆控制器接入现有 Qt 界面"""

from typing import Optional

from src.common.consist import CONSIST_4M2T
from src.common.track_position import ITrackQuery, TrackPosition
from src.track.data import TrackData
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.energy import EnergyCalculator, EnergyStepReport, EnergyTripSummary
from src.vehicle.enums import ControlLevel, DoorSide, RunningMode, LoadLevel, CONTROL_LEVEL_MAP
from src.vehicle.environment import MockEnvironment, WeatherType
from src.vehicle.vehicle_controller import VehicleController


class TrackDataQuery(ITrackQuery):
    """把 TrackData 的绝对米坐标查询适配为车辆模块需要的 TrackPosition 查询"""

    def __init__(self, track: TrackData):
        self.track = track
        self.external_speed_limit: Optional[float] = None
        self._active_route = None

    def set_track_data(self, track: TrackData):
        """切换线路数据源"""
        self.track = track

    def get_speed_limit(self, pos: TrackPosition) -> float:
        abs_pos = max(0.0, self.to_absolute(pos))
        limit = self.track.get_speed_limit_at(abs_pos)
        if self.external_speed_limit is not None:
            return min(limit, self.external_speed_limit)
        return limit

    def get_gradient(self, pos: TrackPosition) -> float:
        return self.track.get_gradient_at(max(0.0, self.to_absolute(pos)))

    def get_is_tunnel(self, pos: TrackPosition) -> bool:
        return False

    def get_curve_radius(self, pos: TrackPosition) -> Optional[float]:
        return None

    def set_active_route(self, route):
        """保存活动进路，兼容统一线路查询接口。"""
        self._active_route = route

    def get_active_route(self):
        """返回当前活动进路。"""
        return self._active_route

    def advance_position(self, pos: TrackPosition, distance: float) -> TrackPosition:
        return self.from_absolute(self.to_absolute(pos) + distance,
                                  hint_seg_id=pos.segment_id)

    def to_absolute(self, pos: TrackPosition) -> float:
        seg = self.track._seg_map.get(pos.segment_id)
        if seg:
            return seg.abs_start + pos.offset
        return pos.offset

    def from_absolute(self, abs_pos: float, hint_seg_id: Optional[int] = None) -> TrackPosition:
        # 允许尾部车辆在发车初始阶段位于线路起点前方，避免编组被强行压缩到 0m。
        if abs_pos < 0:
            # 有 hint 时在同链上找 abs_start≈0 的根段，避免并行链歧义
            if hint_seg_id is not None and hint_seg_id in self.track._seg_map:
                chain_ids = self.track.get_chain_ids(hint_seg_id)
                for seg in self.track.segments:
                    if seg.seg_id in chain_ids and abs(seg.abs_start) < 0.01:
                        return TrackPosition(seg.seg_id, abs_pos)
                return TrackPosition(hint_seg_id, abs_pos)
            first_seg = self.track.segments[0].seg_id if self.track.segments else 1
            return TrackPosition(first_seg, abs_pos)

        # 收集所有覆盖该绝对位置的候选段（处理并行链重叠坐标）
        candidates = []
        for seg in self.track.segments:
            if seg.abs_start <= abs_pos < seg.abs_start + seg.length:
                candidates.append(seg)

        if len(candidates) == 1:
            seg = candidates[0]
            return TrackPosition(seg.seg_id, abs_pos - seg.abs_start)

        if len(candidates) > 1 and hint_seg_id is not None:
            # 链消歧：优先选择与 hint_seg_id 在同一链上的段
            chain_ids = self.track.get_chain_ids(hint_seg_id)
            for seg in candidates:
                if seg.seg_id in chain_ids:
                    return TrackPosition(seg.seg_id, abs_pos - seg.abs_start)

        # 兜底：返回第一个候选
        if candidates:
            seg = candidates[0]
            return TrackPosition(seg.seg_id, abs_pos - seg.abs_start)

        if self.track.segments:
            last = max(self.track.segments, key=lambda s: s.abs_start + s.length)
            return TrackPosition(last.seg_id, last.length)
        return TrackPosition(1, 0.0)

    def _build_chain_ids(self, seed_seg_id: int) -> "set[int]":
        """[已废弃] 请使用 self.track.get_chain_ids()。"""
        return self.track.get_chain_ids(seed_seg_id)


class VehicleUiAdapter:
    """保持旧 UI 字段接口，同时委托新 VehicleController 执行动力学计算"""

    def __init__(self, track: TrackData):
        self.dt: float = 0.1
        self.current_gradient: float = 0.0
        self._current_speed_limit: float = 22.0
        self.control_level: ControlLevel = ControlLevel.COAST
        self.track_query = TrackDataQuery(track)
        self.environment = MockEnvironment(WeatherType.DRY, self.track_query)
        self.controller = VehicleController(CONSIST_4M2T, self.track_query, self.environment)
        self.last_report = None

        # 能耗计算器
        self.energy_calc = EnergyCalculator(
            num_cars=len(CONSIST_4M2T),
            load_level=LoadLevel.AW2,
        )

    @property
    def position(self) -> float:
        if not self.controller.states:
            return 0.0
        return max(0.0, self.track_query.to_absolute(self.controller.states[0].position))

    @property
    def speed(self) -> float:
        return self.controller.head_speed

    @property
    def acceleration(self) -> float:
        """整列平均加速度，供旧 UI 主显示和日志使用"""
        if not self.controller.states:
            return 0.0
        return sum(s.acceleration for s in self.controller.states) / len(self.controller.states)

    @property
    def head_acceleration(self) -> float:
        """头车加速度，用于多体动力详情显示"""
        if not self.controller.states:
            return 0.0
        return self.controller.states[0].acceleration

    @property
    def running_mode(self) -> RunningMode:
        return self.controller.running_mode

    @running_mode.setter
    def running_mode(self, mode: RunningMode):
        self.controller.set_running_mode(mode)

    @property
    def current_speed_limit(self) -> float:
        return self._current_speed_limit

    @current_speed_limit.setter
    def current_speed_limit(self, value: float):
        self._current_speed_limit = value
        self.track_query.external_speed_limit = value

    @property
    def left_door_open(self) -> bool:
        return self.controller.left_door_open

    @property
    def right_door_open(self) -> bool:
        return self.controller.right_door_open

    @property
    def door_side(self) -> DoorSide:
        return self.controller.door_side

    @property
    def consist_summary(self) -> str:
        return repr(self.controller.consist)

    @property
    def max_coupler_force_kn(self) -> float:
        if not self.last_report:
            return 0.0
        return self.last_report.max_coupler_force / 1000.0

    def set_track_data(self, track: TrackData):
        """替换线路数据并重置车辆状态"""
        self.track_query.set_track_data(track)
        self.reset()

    def apply_traction(self, level: ControlLevel):
        """手动模式下设置司控器级位"""
        if self.running_mode != RunningMode.MANUAL:
            return
        self.set_control_level_direct(level)

    def set_control_level_direct(self, level: ControlLevel):
        """直接设置司控器级位，供自动驾驶和安全逻辑使用"""
        self.control_level = level
        throttle, brake = CONTROL_LEVEL_MAP[level]
        self.controller.set_throttle(throttle)
        self.controller.set_brake(brake)

    def open_door(self, side: DoorSide):
        return self.controller.open_door(side)

    def close_door(self):
        self.controller.close_door()

    def doors_closed(self) -> bool:
        return self.controller.doors_closed()

    def step(self):
        self.last_report = self.controller.step(self.dt)
        self.energy_calc.step(self.last_report)

    def get_speed_kmh(self) -> float:
        return self.speed * 3.6

    # ── 能耗查询 ───────────────────────────────────────────────

    @property
    def energy_traction_kwh(self) -> float:
        """累积牵引电耗 (kWh)。"""
        return self.energy_calc.traction_kwh

    @property
    def energy_regen_kwh(self) -> float:
        """累积再生回收 (kWh)。"""
        return self.energy_calc.regen_kwh

    @property
    def energy_aux_kwh(self) -> float:
        """累积辅助电耗 (kWh)。"""
        return self.energy_calc.aux_kwh

    @property
    def energy_net_kwh(self) -> float:
        """累积净电耗 (kWh)。"""
        return self.energy_calc.net_kwh

    @property
    def energy_friction_loss_kwh(self) -> float:
        """累积摩擦制动热损 (kWh)。"""
        return self.energy_calc._friction_brake_loss_j / 3_600_000

    @property
    def energy_regen_ratio(self) -> float:
        """再生能量回收率。"""
        t = self.energy_calc.traction_kwh
        return self.energy_calc.regen_kwh / t if t > 0 else 0.0

    @property
    def energy_last_step(self) -> Optional[EnergyStepReport]:
        """最近一步的能耗报告。"""
        return self.energy_calc.last_step

    @property
    def energy_step_history(self):
        """逐步能耗历史。"""
        return self.energy_calc.step_history

    def energy_summary(self) -> EnergyTripSummary:
        """生成行程能耗汇总。"""
        head_abs = self.position
        total_mass = self.controller.consist.total_mass
        return self.energy_calc.summary(
            distance_m=head_abs,
            total_mass_kg=total_mass,
        )

    def reset_energy(self):
        """重置能耗计算器。"""
        self.energy_calc.reset()

    def reset(self):
        self.controller.reset_states(start_segment_id=1, start_offset=0.0)
        self.control_level = ControlLevel.COAST
        self.current_gradient = 0.0
        self._current_speed_limit = 22.0
        self.track_query.external_speed_limit = None
        self.last_report = None
        self.reset_energy()


class ManualControlAdapter:
    """现有控制面板使用的手动控制接口"""

    def __init__(self, vehicle: VehicleUiAdapter):
        self.vehicle = vehicle

    def set_traction(self):
        self.vehicle.apply_traction(ControlLevel.FULL_TRACTION)

    def set_coast(self):
        self.vehicle.apply_traction(ControlLevel.COAST)

    def set_service_brake(self):
        self.vehicle.apply_traction(ControlLevel.SERVICE_BRAKE)

    def set_full_brake(self):
        self.vehicle.apply_traction(ControlLevel.FULL_BRAKE)

    def set_emergency_brake(self):
        self.vehicle.apply_traction(ControlLevel.EMERGENCY_BRAKE)

    def open_left_door(self):
        return self.vehicle.open_door(DoorSide.LEFT)

    def open_right_door(self):
        return self.vehicle.open_door(DoorSide.RIGHT)

    def close_door(self):
        self.vehicle.close_door()


class UiAutoControlAdapter:
    """把旧 UI 的自动驾驶调用适配到新的 AutoDriveController"""

    def __init__(self, vehicle: VehicleUiAdapter):
        self.vehicle = vehicle
        self._auto = AutoDriveController(vehicle.controller)

    def set_target(self, position: float):
        self._auto.set_target(self.vehicle.track_query.from_absolute(position))

    def step(self):
        self._auto.step()

    def is_stopped(self) -> bool:
        return self.vehicle.controller.is_stopped
