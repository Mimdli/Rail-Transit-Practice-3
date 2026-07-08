"""CarConfig 单元测试 — 阶段 2"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.car_config import (
    CarConfig, CouplerConfig,
    MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG,
    DEFAULT_COUPLER_CONFIG,
)


# ── CouplerConfig ─────────────────────────────────────────────

def test_coupler_config_defaults():
    """默认车钩参数正确。"""
    c = CouplerConfig()
    assert c.stiffness == 1e7
    assert c.damping == 1e5
    assert c.slack == 0.02
    assert c.max_force == 2e6


def test_coupler_config_custom():
    """自定义车钩参数。"""
    c = CouplerConfig(stiffness=5e6, damping=8e4, slack=0.01, max_force=1.5e6)
    assert c.stiffness == 5e6
    assert c.damping == 8e4
    assert c.slack == 0.01
    assert c.max_force == 1.5e6


def test_default_coupler_config():
    """全局默认车钩配置可用。"""
    assert DEFAULT_COUPLER_CONFIG.stiffness == 1e7
    assert DEFAULT_COUPLER_CONFIG.damping == 1e5


# ── CarConfig ─────────────────────────────────────────────────

def test_motor_car_preset():
    """动车预设值正确。"""
    mc = MOTOR_CAR_CONFIG
    assert mc.name == "MotorCar"
    assert mc.mass == 60000.0
    assert mc.length == 19.5
    assert mc.is_motor is True
    assert mc.max_traction_force == 80000.0
    assert mc.max_service_brake_force == 80000.0
    assert mc.max_emergency_brake_force == 100000.0
    assert mc.traction_transition_speed == 10.0
    assert mc.construction_speed == 22.22


def test_trailer_car_preset():
    """拖车预设值正确。"""
    tc = TRAILER_CAR_CONFIG
    assert tc.name == "TrailerCar"
    assert tc.mass == 46000.0
    assert tc.is_motor is False
    assert tc.max_traction_force == 0.0
    assert tc.max_service_brake_force == 65000.0
    assert tc.max_emergency_brake_force == 80000.0


def test_davis_coefficients_motor():
    """动车 Davis 系数正确。"""
    mc = MOTOR_CAR_CONFIG
    assert mc.davis_A == 1200.0
    assert mc.davis_B == 30.0
    assert mc.davis_C == 3.0


def test_davis_coefficients_trailer():
    """拖车 Davis 系数正确。"""
    tc = TRAILER_CAR_CONFIG
    assert tc.davis_A == 900.0
    assert tc.davis_B == 25.0
    assert tc.davis_C == 2.5


def test_custom_car_config():
    """自定义车辆参数。"""
    c = CarConfig(
        name="TestCar",
        mass=50000.0,
        length=20.0,
        is_motor=True,
        davis_A=1000.0,
        davis_B=20.0,
        davis_C=2.0,
        max_traction_force=60000.0,
        traction_transition_speed=8.0,
        construction_speed=25.0,
        max_service_brake_force=60000.0,
        max_emergency_brake_force=80000.0,
        tunnel_resistance_factor=1.5,
    )
    assert c.mass == 50000.0
    assert c.length == 20.0
    assert c.tunnel_resistance_factor == 1.5
    assert c.traction_transition_speed == 8.0


def test_car_config_fields():
    """验证 CarConfig 所有字段可访问。"""
    mc = MOTOR_CAR_CONFIG
    fields = [
        "name", "mass", "length", "is_motor",
        "davis_A", "davis_B", "davis_C",
        "max_traction_force", "traction_transition_speed", "construction_speed",
        "max_service_brake_force", "max_emergency_brake_force",
        "tunnel_resistance_factor",
    ]
    for f in fields:
        assert hasattr(mc, f), f"Missing field: {f}"


if __name__ == "__main__":
    test_coupler_config_defaults()
    test_coupler_config_custom()
    test_default_coupler_config()
    test_motor_car_preset()
    test_trailer_car_preset()
    test_davis_coefficients_motor()
    test_davis_coefficients_trailer()
    test_custom_car_config()
    test_car_config_fields()
    print("All car_config tests passed!")
