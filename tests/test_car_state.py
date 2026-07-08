"""CarState 单元测试 — 阶段 3"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.car_state import CarState
from src.common.track_position import TrackPosition


def test_create_car_state():
    """创建 CarState 并验证字段。"""
    pos = TrackPosition(segment_id=1, offset=100.0)
    s = CarState(position=pos, velocity=15.0, acceleration=0.5)
    assert s.position.segment_id == 1
    assert s.position.offset == 100.0
    assert s.velocity == 15.0
    assert s.acceleration == 0.5


def test_default_values():
    """默认值为零。"""
    pos = TrackPosition(1, 0.0)
    s = CarState(position=pos)
    assert s.velocity == 0.0
    assert s.acceleration == 0.0


def test_negative_velocity_allowed():
    """速度可以为负（物理上表示反向运行，建模允许）。"""
    pos = TrackPosition(1, 0.0)
    s = CarState(position=pos, velocity=-2.0)
    assert s.velocity == -2.0


def test_copy_independence():
    """copy() 产生独立副本，修改原对象不影响副本。"""
    pos = TrackPosition(1, 500.0)
    original = CarState(position=pos, velocity=10.0, acceleration=0.3)
    cp = original.copy()

    # 修改原对象
    original.velocity = 20.0
    original.acceleration = -1.0
    original.position.offset = 600.0

    # 副本不变
    assert cp.velocity == 10.0
    assert cp.acceleration == 0.3
    assert cp.position.offset == 500.0


def test_copy_deep_position():
    """copy() 的 position 是独立对象。"""
    pos = TrackPosition(2, 300.0)
    s = CarState(position=pos, velocity=5.0)
    cp = s.copy()

    # 修改原对象 position
    s.position.segment_id = 3
    s.position.offset = 999.0

    assert cp.position.segment_id == 2
    assert cp.position.offset == 300.0


def test_high_precision():
    """数值精度正确（浮点比较）。"""
    pos = TrackPosition(1, 1234.567)
    s = CarState(position=pos, velocity=22.222, acceleration=-1.234)
    assert abs(s.velocity - 22.222) < 1e-10
    assert abs(s.acceleration - (-1.234)) < 1e-10
    assert abs(s.position.offset - 1234.567) < 1e-10


if __name__ == "__main__":
    test_create_car_state()
    test_default_values()
    test_negative_velocity_allowed()
    test_copy_independence()
    test_copy_deep_position()
    test_high_precision()
    print("All car_state tests passed!")
