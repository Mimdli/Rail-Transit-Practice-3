"""ForceReport 单元测试 — 阶段 8"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.track_position import TrackPosition
from src.vehicle.force_report import CarForceReport, ForceReport


def test_car_force_report_defaults():
    """CarForceReport 默认值正确。"""
    r = CarForceReport()
    assert r.car_index == 0
    assert r.velocity == 0.0
    assert r.acceleration == 0.0
    assert r.davis_resistance == 0.0
    assert r.tractive_force == 0.0
    assert r.brake_force == 0.0
    assert r.net_force == 0.0


def test_car_force_report_fields():
    """CarForceReport 所有字段可设置和读取。"""
    pos = TrackPosition(2, 350.0)
    r = CarForceReport(
        car_index=3,
        position=pos,
        velocity=15.0,
        acceleration=0.5,
        davis_resistance=-2500.0,
        grade_resistance=-15000.0,
        tunnel_resistance=-750.0,
        tractive_force=53333.0,
        brake_force=0.0,
        coupler_force_front=50000.0,
        coupler_force_rear=-30000.0,
        net_coupler_force=20000.0,
        total_external_force=35083.0,
        net_force=55083.0,
    )
    assert r.car_index == 3
    assert r.position.segment_id == 2
    assert r.position.offset == 350.0
    assert r.velocity == 15.0
    assert r.tractive_force == 53333.0
    assert r.coupler_force_front == 50000.0
    assert r.net_force == 55083.0


def test_force_report_empty():
    """空 ForceReport 属性返回安全默认值。"""
    fr = ForceReport(step=1, timestamp=0.5, dt=0.033)
    assert fr.step == 1
    assert fr.timestamp == 0.5
    assert fr.head_velocity == 0.0
    assert fr.max_coupler_force == 0.0
    assert fr.total_tractive_force == 0.0


def test_force_report_head_properties():
    """ForceReport 头车属性正确。"""
    pos0 = TrackPosition(1, 100.0)
    pos1 = TrackPosition(1, 119.5)
    cars = [
        CarForceReport(car_index=0, position=pos0, velocity=20.0),
        CarForceReport(car_index=1, position=pos1, velocity=19.8),
    ]
    fr = ForceReport(step=5, timestamp=1.0, dt=0.033, n_substeps=33, cars=cars)
    assert fr.head_position.offset == 100.0
    assert abs(fr.head_velocity - 20.0) < 1e-9


def test_force_report_max_coupler_force():
    """max_coupler_force 正确计算。"""
    cars = [
        CarForceReport(car_index=0, coupler_force_front=0.0, coupler_force_rear=50000.0),
        CarForceReport(car_index=1, coupler_force_front=-50000.0, coupler_force_rear=-30000.0),
        CarForceReport(car_index=2, coupler_force_front=30000.0, coupler_force_rear=0.0),
    ]
    fr = ForceReport(step=1, timestamp=0.0, dt=0.033, cars=cars)
    assert fr.max_coupler_force == 50000.0


def test_force_report_total_tractive_force():
    """total_tractive_force 正确求和。"""
    cars = [
        CarForceReport(car_index=0, tractive_force=80000.0),
        CarForceReport(car_index=1, tractive_force=0.0),      # 拖车
        CarForceReport(car_index=2, tractive_force=80000.0),
    ]
    fr = ForceReport(step=1, timestamp=0.0, dt=0.033, cars=cars)
    assert fr.total_tractive_force == 160000.0


def test_force_report_n_substeps():
    """n_substeps 记录微步数量。"""
    fr = ForceReport(step=10, timestamp=0.33, dt=0.033, n_substeps=33)
    assert fr.n_substeps == 33


if __name__ == "__main__":
    test_car_force_report_defaults()
    test_car_force_report_fields()
    test_force_report_empty()
    test_force_report_head_properties()
    test_force_report_max_coupler_force()
    test_force_report_total_tractive_force()
    test_force_report_n_substeps()
    print("All force_report tests passed!")
