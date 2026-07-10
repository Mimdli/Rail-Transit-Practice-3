"""调度领域模型：交路、列车运行实例和运行状态。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.track.adapter import TrackDataAdapter
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.vehicle_controller import VehicleController


class TrainStatus(Enum):
    """调度层关注的列车状态。"""

    MANUAL = "人工驾驶"
    WAITING = "待发"
    RUNNING = "运行"
    DWELLING = "停站"
    HELD = "扣车"
    BLOCKED = "进路等待"
    EMERGENCY_STOP = "紧急停车"
    TURNING_BACK = "折返换端"
    COMPLETED = "交路完成"


@dataclass(frozen=True)
class ServicePlan:
    """按车站顺序定义的运营交路。"""

    plan_id: str
    name: str
    station_ids: tuple[int, ...]
    turnback: bool = False
    dwell_time: float = 5.0

    def __post_init__(self):
        if len(self.station_ids) < 2:
            raise ValueError("交路至少需要两个车站")
        if self.dwell_time < 0:
            raise ValueError("停站时间不能为负数")


@dataclass
class TrainRuntime:
    """一列车的动力学、自动驾驶与调度状态集合。"""

    train_id: str
    controller: VehicleController
    auto_drive: AutoDriveController
    track_adapter: TrackDataAdapter
    status: TrainStatus = TrainStatus.WAITING
    service_plan: Optional[ServicePlan] = None
    plan_index: int = 0
    plan_step: int = 1
    target_station_id: Optional[int] = None
    dwell_remaining: float = 0.0
    held: bool = False
    emergency: bool = False
    blocked_reason: str = ""
    reserved_segments: tuple[int, ...] = field(default_factory=tuple)
    completed_cycles: int = 0

    @property
    def head_abs(self) -> float:
        if not self.controller.states:
            return 0.0
        return self.track_adapter.to_absolute(self.controller.states[0].position)

    @property
    def speed_kmh(self) -> float:
        return self.controller.head_speed_kmh

    @property
    def direction_label(self) -> str:
        return "下行" if self.controller.direction > 0 else "上行"
