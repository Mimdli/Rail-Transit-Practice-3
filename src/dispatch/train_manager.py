"""多列车生命周期管理。"""

from collections import OrderedDict
from typing import Iterable, Optional

from src.common.consist import CONSIST_4M2T
from src.track.adapter import TrackDataAdapter
from src.track.data import TrackData
from src.track.loader import TrackLoader
from src.track.route import Route
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.environment import MockEnvironment, WeatherType
from src.vehicle.vehicle_controller import VehicleController

from .models import TrainRuntime, TrainStatus


class TrainManager:
    """创建、查询和删除相互独立的列车运行实例。"""

    def __init__(self, track: TrackData, max_trains: int = 20):
        self.track = track
        self.max_trains = max_trains
        self._trains: "OrderedDict[str, TrainRuntime]" = OrderedDict()

    def register_existing(self, train_id: str, controller: VehicleController,
                          auto_drive: AutoDriveController,
                          adapter: TrackDataAdapter) -> TrainRuntime:
        """将主界面已有控制器注册为调度列车，避免重复创建1车。"""
        if train_id in self._trains:
            raise ValueError(f"列车编号已存在: {train_id}")
        runtime = TrainRuntime(
            train_id, controller, auto_drive, adapter,
            status=TrainStatus.MANUAL,
        )
        self._trains[train_id] = runtime
        return runtime

    def add_train(self, train_id: str, start_station_id: Optional[int] = None,
                  direction: int = 1) -> TrainRuntime:
        train_id = train_id.strip()
        if not train_id:
            raise ValueError("列车编号不能为空")
        if train_id in self._trains:
            raise ValueError(f"列车编号已存在: {train_id}")
        if len(self._trains) >= self.max_trains:
            raise ValueError(f"最多允许 {self.max_trains} 列车")

        adapter = TrackDataAdapter(self.track)
        env = MockEnvironment(WeatherType.DRY, adapter)
        controller = VehicleController(CONSIST_4M2T, adapter, env)
        controller.direction = 1 if direction >= 0 else -1

        start_abs = 0.0
        if start_station_id is not None:
            station = self.get_station(start_station_id)
            start_abs = station.position
        start = adapter.from_absolute(start_abs)
        controller.reset_states(start.segment_id, start.offset)

        auto_drive = AutoDriveController(controller)
        routes = (TrackLoader.create_demo_routes() if len(self.track.segments) <= 10
                  else [Route(0, "自动（数据库拓扑）", [])])
        auto_drive.set_available_routes(routes)
        runtime = TrainRuntime(train_id, controller, auto_drive, adapter)
        self._trains[train_id] = runtime
        return runtime

    def remove_train(self, train_id: str) -> TrainRuntime:
        if train_id not in self._trains:
            raise KeyError(f"列车不存在: {train_id}")
        return self._trains.pop(train_id)

    def clear(self, keep_train_id: Optional[str] = None):
        if keep_train_id is None:
            self._trains.clear()
            return
        kept = self._trains.get(keep_train_id)
        self._trains.clear()
        if kept is not None:
            self._trains[keep_train_id] = kept

    def get(self, train_id: str) -> Optional[TrainRuntime]:
        return self._trains.get(train_id)

    def require(self, train_id: str) -> TrainRuntime:
        runtime = self.get(train_id)
        if runtime is None:
            raise KeyError(f"列车不存在: {train_id}")
        return runtime

    def values(self) -> Iterable[TrainRuntime]:
        return tuple(self._trains.values())

    def ids(self) -> tuple[str, ...]:
        return tuple(self._trains.keys())

    def __len__(self) -> int:
        return len(self._trains)

    def get_station(self, station_id: int):
        for station in self.track.stations:
            if station.station_id == station_id:
                return station
        raise ValueError(f"车站不存在: {station_id}")
