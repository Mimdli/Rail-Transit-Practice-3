"""车辆 UI 适配层测试"""

from src.track.loader import TrackLoader
from src.vehicle.enums import ControlLevel
from src.vehicle.ui_adapter import VehicleUiAdapter


def test_vehicle_ui_adapter_steps_with_new_controller():
    """UI 适配层应通过新 VehicleController 推动车辆运动"""
    track = TrackLoader().load_demo_data()
    vehicle = VehicleUiAdapter(track)

    vehicle.apply_traction(ControlLevel.FULL_TRACTION)
    for _ in range(20):
        vehicle.current_speed_limit = 22.0
        vehicle.step()

    assert vehicle.speed > 0.0
    assert vehicle.position > 0.0
    assert "TrainConsist" in vehicle.consist_summary
    assert vehicle.last_report is not None


def test_vehicle_ui_adapter_updates_track_data():
    """切换线路数据时应重置车辆并更新线路查询"""
    track = TrackLoader().load_demo_data()
    vehicle = VehicleUiAdapter(track)
    vehicle.apply_traction(ControlLevel.FULL_TRACTION)
    vehicle.step()

    vehicle.set_track_data(track)

    assert vehicle.position == 0.0
    assert vehicle.speed == 0.0
    assert vehicle.track_query.track is track


def test_vehicle_ui_adapter_reports_average_acceleration():
    """旧 UI 主加速度口径应使用整列平均值"""
    track = TrackLoader().load_demo_data()
    vehicle = VehicleUiAdapter(track)
    vehicle.apply_traction(ControlLevel.FULL_TRACTION)
    vehicle.step()

    expected = sum(s.acceleration for s in vehicle.controller.states) / len(vehicle.controller.states)

    assert vehicle.acceleration == expected
    assert vehicle.head_acceleration == vehicle.controller.states[0].acceleration
