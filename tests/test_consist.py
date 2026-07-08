"""TrainConsist 单元测试 — 阶段 4"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.consist import (
    TrainConsist, CONSIST_4M2T, CONSIST_6M0T, CONSIST_1M4T,
)
from src.common.car_config import CarConfig, MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG


def test_consist_length():
    """编组长度正确。"""
    assert len(CONSIST_4M2T) == 6
    assert len(CONSIST_6M0T) == 6
    assert len(CONSIST_1M4T) == 5


def test_consist_indexing():
    """索引访问返回正确的 CarConfig。"""
    c = TrainConsist([MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG, MOTOR_CAR_CONFIG])
    assert c[0].is_motor is True
    assert c[1].is_motor is False
    assert c[2].is_motor is True


def test_is_motor_4M2T():
    """4M2T 动车/拖车索引正确。"""
    c = CONSIST_4M2T
    # M-T-M-M-T-M
    expected = [True, False, True, True, False, True]
    for i, exp in enumerate(expected):
        assert c.is_motor(i) == exp, f"Index {i}: expected motor={exp}"


def test_is_motor_6M0T():
    """全动车编组：每节都是动车。"""
    for i in range(len(CONSIST_6M0T)):
        assert CONSIST_6M0T.is_motor(i) is True


def test_is_motor_1M4T():
    """1M4T：仅第 1 节是动车。"""
    assert CONSIST_1M4T.is_motor(0) is True
    for i in range(1, 5):
        assert CONSIST_1M4T.is_motor(i) is False


def test_total_mass():
    """总质量计算正确。"""
    c = CONSIST_4M2T
    expected = 4 * 60000.0 + 2 * 46000.0  # 240000 + 92000 = 332000
    assert c.total_mass == expected


def test_total_length():
    """总长度计算正确。"""
    c = CONSIST_4M2T
    assert c.total_length == 6 * 19.5  # 117.0 m


def test_motor_trailer_count():
    """动车/拖车计数正确。"""
    assert CONSIST_4M2T.motor_count == 4
    assert CONSIST_4M2T.trailer_count == 2
    assert CONSIST_6M0T.motor_count == 6
    assert CONSIST_6M0T.trailer_count == 0
    assert CONSIST_1M4T.motor_count == 1
    assert CONSIST_1M4T.trailer_count == 4


def test_empty_consist_raises():
    """空编组抛出 ValueError。"""
    try:
        TrainConsist([])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_repr():
    """repr 包含基本信息。"""
    r = repr(CONSIST_4M2T)
    assert "4M2T" in r
    assert "6" in r


def test_custom_consist():
    """自定义编组。"""
    custom_car = CarConfig(name="Custom", mass=55000.0, length=20.0, is_motor=True)
    c = TrainConsist([custom_car, custom_car])
    assert len(c) == 2
    assert c.total_mass == 110000.0
    assert c.motor_count == 2


if __name__ == "__main__":
    test_consist_length()
    test_consist_indexing()
    test_is_motor_4M2T()
    test_is_motor_6M0T()
    test_is_motor_1M4T()
    test_total_mass()
    test_total_length()
    test_motor_trailer_count()
    test_empty_consist_raises()
    test_repr()
    test_custom_consist()
    print("All consist tests passed!")
