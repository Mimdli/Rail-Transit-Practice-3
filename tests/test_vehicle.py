"""车辆模块单元测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.vehicle.model import VehicleModel, ControlLevel, RunningMode, DoorSide


def test_initial_state():
    v = VehicleModel()
    assert v.speed == 0.0
    assert v.position == 0.0
    assert v.control_level == ControlLevel.COAST
    assert v.doors_closed()


def test_traction_accelerates():
    v = VehicleModel()
    v.apply_traction(ControlLevel.FULL_TRACTION)
    for _ in range(10):
        v.step()
    assert v.speed > 0


def test_brake_decelerates():
    v = VehicleModel()
    v.apply_traction(ControlLevel.FULL_TRACTION)
    for _ in range(20):
        v.step()
    v.apply_traction(ControlLevel.EMERGENCY_BRAKE)
    for _ in range(30):
        v.step()
    assert v.speed == 0.0


def test_speed_limit():
    v = VehicleModel()
    v.current_speed_limit = 5.0
    v.apply_traction(ControlLevel.FULL_TRACTION)
    for _ in range(50):
        v.step()
    assert v.speed <= 5.1


def test_door_interlock():
    v = VehicleModel()
    v.speed = 5.0  # 列车运动中
    assert not v.open_door(DoorSide.LEFT)  # 速度不为0时不能开门

    v.speed = 0.0  # 停车后可以开门
    assert v.open_door(DoorSide.LEFT)
    assert v.left_door_open
    assert not v.doors_closed()

    v.close_door()
    assert v.doors_closed()


def test_reset():
    v = VehicleModel()
    v.apply_traction(ControlLevel.FULL_TRACTION)
    for _ in range(10):
        v.step()
    v.reset()
    assert v.speed == 0.0
    assert v.position == 0.0
    assert v.control_level == ControlLevel.COAST


if __name__ == "__main__":
    test_initial_state()
    test_traction_accelerates()
    test_brake_decelerates()
    test_speed_limit()
    test_door_interlock()
    test_reset()
    print("All vehicle tests passed!")
