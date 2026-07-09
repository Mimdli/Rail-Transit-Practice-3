"""能耗计算模块测试

覆盖 calc_regen_efficiency、EnergyStepReport、EnergyCalculator、EnergyTripSummary。
"""

import math
import pytest
from dataclasses import dataclass, field
from typing import List

from src.vehicle.energy import (
    calc_regen_efficiency,
    EnergyStepReport,
    EnergyTripSummary,
    EnergyCalculator,
)
from src.vehicle.force_report import ForceReport, CarForceReport
from src.vehicle.enums import LoadLevel
from src.common.track_position import TrackPosition


# ═══════════════════════════════════════════════════════════════
# calc_regen_efficiency
# ═══════════════════════════════════════════════════════════════

class TestRegenEfficiency:
    """再生制动能量回收效率 η_regen(v)"""

    def test_stop_returns_zero(self):
        """v=0 时效率为零。"""
        assert calc_regen_efficiency(0.0) == 0.0

    def test_very_low_speed_returns_zero(self):
        """反接制动区 (0-3 km/h)：效率为零。"""
        # 1 km/h = 0.278 m/s
        assert calc_regen_efficiency(0.2) == 0.0
        assert calc_regen_efficiency(0.5) == 0.0
        # 2.9 km/h = 0.806 m/s
        assert calc_regen_efficiency(0.8) == 0.0

    def test_transition_zone(self):
        """过渡区 (3-10 km/h)：效率线性上升。"""
        # v = 3 km/h = 0.833 m/s → boundary, 效率应该接近0
        eta_3 = calc_regen_efficiency(0.834)  # just above 3 km/h
        assert eta_3 > 0.0

        # v = 6.5 km/h = 1.806 m/s → midpoint of transition
        eta_mid = calc_regen_efficiency(1.806)
        # expected: (6.5 - 3.0) / 7.0 * 0.85 = 0.425
        assert 0.40 < eta_mid < 0.45

        # v = 10 km/h = 2.778 m/s → top of transition
        eta_10 = calc_regen_efficiency(2.778)
        assert abs(eta_10 - 0.85) < 0.01

    def test_normal_zone_plateau(self):
        """正常再生区 (10+ km/h)：效率 0.85-0.90。"""
        # v = 20 m/s = 72 km/h
        eta_72 = calc_regen_efficiency(20.0)
        # expected: 0.85 + (72-10)/60 * 0.05 = 0.85 + 0.0517 = 0.9017 → capped 0.90
        assert 0.89 <= eta_72 <= 0.91

        # v = 40 km/h = 11.11 m/s
        eta_40 = calc_regen_efficiency(11.11)
        # expected: 0.85 + (40-10)/60 * 0.05 = 0.875
        assert 0.87 < eta_40 < 0.88

    def test_efficiency_monotonic(self):
        """效率在 0-80 km/h 范围内单调不减。"""
        prev = 0.0
        for v_ms in [0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0]:
            eta = calc_regen_efficiency(v_ms)
            assert eta >= prev, f"v={v_ms:.1f} m/s: eta={eta:.4f} < prev={prev:.4f}"
            prev = eta

    def test_negative_velocity_symmetric(self):
        """效率只取决于速度幅值，与方向无关。"""
        assert calc_regen_efficiency(10.0) == calc_regen_efficiency(-10.0)


# ═══════════════════════════════════════════════════════════════
# EnergyStepReport
# ═══════════════════════════════════════════════════════════════

class TestEnergyStepReport:
    """单步能耗报告"""

    def test_net_energy(self):
        r = EnergyStepReport(
            traction_energy_j=1_000_000,
            regen_energy_j=300_000,
            aux_energy_j=50_000,
            dt=1.0,
        )
        # net = 1,000,000 - 300,000 + 50,000 = 750,000 J
        assert r.net_energy_j == 750_000

    def test_power_calculation(self):
        r = EnergyStepReport(
            traction_energy_j=360_000,  # 360 kJ in 1s
            dt=1.0,
        )
        assert r.traction_power_kw == 360.0

        r2 = EnergyStepReport(
            traction_energy_j=18_000,  # 18 kJ in 0.1s
            dt=0.1,
        )
        assert r2.traction_power_kw == 180.0

    def test_power_zero_when_dt_zero(self):
        r = EnergyStepReport(dt=0.0, traction_energy_j=1000)
        assert r.traction_power_kw == 0.0


# ═══════════════════════════════════════════════════════════════
# 辅助：构造 ForceReport
# ═══════════════════════════════════════════════════════════════

def _make_car_report(**kwargs) -> CarForceReport:
    """创建一个带默认值的 CarForceReport。"""
    defaults = dict(
        car_index=0,
        position=TrackPosition(1, 100.0),
        velocity=15.0,
        acceleration=0.5,
    )
    defaults.update(kwargs)
    return CarForceReport(**defaults)


def _make_force_report(cars: List[CarForceReport], dt: float = 0.033,
                       step: int = 1, timestamp: float = 0.0) -> ForceReport:
    return ForceReport(
        step=step,
        timestamp=timestamp,
        dt=dt,
        n_substeps=33,
        cars=cars,
    )


# ═══════════════════════════════════════════════════════════════
# EnergyCalculator.step()
# ═══════════════════════════════════════════════════════════════

class TestEnergyCalculatorStep:
    """EnergyCalculator 逐步计算"""

    def test_traction_energy_basic(self):
        """牵引时消耗电能 = F × v × dt / η。"""
        calc = EnergyCalculator(num_cars=1, drive_efficiency=0.85)

        # 单节车，牵引力 80000 N，速度 10 m/s，步长 0.1s
        # 轮周机械能 = 80000 × 10 × 0.1 = 80,000 J
        # 电网电能 = 80,000 / 0.85 ≈ 94,117.6 J
        car = _make_car_report(velocity=10.0, tractive_force=80000.0)
        report = _make_force_report([car], dt=0.1)

        step = calc.step(report)
        assert step.traction_energy_j == pytest.approx(80_000 / 0.85, rel=1e-4)
        assert step.regen_energy_j == 0.0
        assert step.friction_brake_loss_j == 0.0

    def test_regen_energy_high_speed(self):
        """高速电制动：再生回收 = F_e × v × dt × η_regen。"""
        calc = EnergyCalculator(num_cars=1)

        # v = 20 m/s = 72 km/h → η_regen ≈ 0.90
        # F_electric_brake = -56000 N (幅值 56000)
        # 机械能 = 56000 × 20 × 0.1 = 112,000 J
        # 回收 = 112,000 × 0.90 = 100,800 J
        expected_eta = calc_regen_efficiency(20.0)  # ≈ 0.90
        car = _make_car_report(velocity=20.0, electric_brake_force=-56000.0)
        report = _make_force_report([car], dt=0.1)

        step = calc.step(report)
        assert step.regen_energy_j == pytest.approx(56000 * 20 * 0.1 * expected_eta, rel=1e-4)
        assert step.traction_energy_j == 0.0

    def test_regen_zero_at_low_speed(self):
        """低速 (< 3 km/h)：再生回收为零（反接制动区）。"""
        calc = EnergyCalculator(num_cars=1)

        # v = 0.5 m/s = 1.8 km/h → 反接制动区，η_regen = 0
        car = _make_car_report(velocity=0.5, electric_brake_force=-56000.0)
        report = _make_force_report([car], dt=0.1)

        step = calc.step(report)
        assert step.regen_energy_j == 0.0
        # 电制动力仍然存在（机械层面），但在能耗模块正确建模为无法回收

    def test_friction_brake_loss(self):
        """摩擦制动全部为热损。"""
        calc = EnergyCalculator(num_cars=1)

        car = _make_car_report(velocity=15.0, friction_brake_force=-24000.0)
        report = _make_force_report([car], dt=0.1)

        step = calc.step(report)
        # 24000 × 15 × 0.1 = 36,000 J
        assert step.friction_brake_loss_j == pytest.approx(24000 * 15 * 0.1, rel=1e-4)
        assert step.regen_energy_j == 0.0  # 摩擦制动无回收

    def test_aux_energy_constant(self):
        """辅助系统功率恒定，与速度无关。"""
        # 4M2T 编组（6节），AW2 = 40 kW/车 → 总 240 kW
        calc = EnergyCalculator(num_cars=6, load_level=LoadLevel.AW2)
        assert calc.aux_power_total == 240_000  # 240 kW

        car = _make_car_report(velocity=0.0, tractive_force=0.0)
        report = _make_force_report([car], dt=1.0)

        step = calc.step(report)
        assert step.aux_energy_j == 240_000  # 240 kW × 1s = 240 kJ

    def test_aux_power_by_load_level(self):
        """不同载荷等级对应不同辅助功率。"""
        levels = {
            LoadLevel.AW0: 30_000,
            LoadLevel.AW1: 35_000,
            LoadLevel.AW2: 40_000,
            LoadLevel.AW3: 45_000,
        }
        for level, expected_per_car in levels.items():
            calc = EnergyCalculator(num_cars=1, load_level=level)
            assert calc.aux_power_total == expected_per_car, \
                f"LoadLevel {level}: expected {expected_per_car}, got {calc.aux_power_total}"

    def test_multi_car_summation(self):
        """多节车的能耗正确求和。"""
        calc = EnergyCalculator(num_cars=6, drive_efficiency=0.85)

        cars = [
            _make_car_report(car_index=0, velocity=10.0, tractive_force=80000.0),
            _make_car_report(car_index=1, velocity=10.0, tractive_force=80000.0),
            _make_car_report(car_index=2, velocity=10.0, tractive_force=0.0),     # 拖车无牵引
            _make_car_report(car_index=3, velocity=10.0, tractive_force=0.0),     # 拖车无牵引
            _make_car_report(car_index=4, velocity=10.0, tractive_force=80000.0),
            _make_car_report(car_index=5, velocity=10.0, tractive_force=80000.0),
        ]
        report = _make_force_report(cars, dt=0.1)

        step = calc.step(report)
        # 4 节动车 × 80000 N × 10 m/s × 0.1s = 320,000 J 机械能
        # 电网 = 320,000 / 0.85 ≈ 376,470.6 J
        expected_mech = 4 * 80000 * 10 * 0.1
        assert step.traction_energy_j == pytest.approx(expected_mech / 0.85, rel=1e-4)

    def test_velocity_sign_irrelevant(self):
        """速度方向不影响能耗计算（均取幅值）。"""
        calc = EnergyCalculator(num_cars=1, drive_efficiency=0.85)

        car_fwd = _make_car_report(velocity=10.0, tractive_force=80000.0)
        car_rev = _make_car_report(velocity=-10.0, tractive_force=-80000.0)

        step_fwd = calc.step(_make_force_report([car_fwd], dt=0.1, step=1))
        calc.reset()
        step_rev = calc.step(_make_force_report([car_rev], dt=0.1, step=1))

        assert step_fwd.traction_energy_j == pytest.approx(step_rev.traction_energy_j, rel=1e-6)


# ═══════════════════════════════════════════════════════════════
# EnergyCalculator 累积与汇总
# ═══════════════════════════════════════════════════════════════

class TestEnergyCalculatorSummary:
    """行程汇总"""

    def test_accumulation_over_multiple_steps(self):
        """多步累积正确。"""
        calc = EnergyCalculator(num_cars=1, drive_efficiency=1.0)

        car = _make_car_report(velocity=10.0, tractive_force=80000.0)
        for i in range(10):
            calc.step(_make_force_report([car], dt=0.1, step=i, timestamp=i * 0.1))

        # 10 步 × 80000 × 10 × 0.1 = 800,000 J = 800 kJ
        assert calc.traction_kwh == pytest.approx(800_000 / 3_600_000, rel=1e-4)

    def test_summary_metrics(self):
        """汇总中的单位能耗指标。"""
        calc = EnergyCalculator(num_cars=6, load_level=LoadLevel.AW2, drive_efficiency=1.0)

        # 模拟一段行程：多步加速→惰行→制动
        car = _make_car_report()
        # 加速阶段：80 kN, v 从 0 到 20 m/s
        for i in range(100):
            v = i * 0.2
            car.velocity = v
            car.tractive_force = 80000.0 if v < 15 else 30000.0
            car.electric_brake_force = 0.0
            car.friction_brake_force = 0.0
            calc.step(_make_force_report([car], dt=0.05, step=i, timestamp=i * 0.05))

        # 制动阶段：电制动，v 从 20 到 0
        for i in range(100):
            v = 20.0 - i * 0.2
            car.velocity = max(v, 0.0)
            car.tractive_force = 0.0
            car.electric_brake_force = -56000.0
            car.friction_brake_force = -24000.0
            calc.step(_make_force_report([car], dt=0.05, step=100 + i, timestamp=5.0 + i * 0.05))

        summary = calc.summary(distance_m=1500.0, total_mass_kg=360_000.0)

        assert summary.trip_time_s == pytest.approx(10.0, abs=0.1)
        assert summary.n_steps == 200
        assert summary.total_traction_kwh > 0
        assert summary.total_regen_kwh > 0
        assert summary.total_friction_brake_loss_kwh > 0
        assert summary.total_aux_kwh > 0

        # 净电耗 = 牵引 - 再生 + 辅助
        expected_net = summary.total_traction_kwh - summary.total_regen_kwh + summary.total_aux_kwh
        assert summary.net_kwh == pytest.approx(expected_net, rel=1e-6)

        # 单位能耗应为正
        assert summary.kwh_per_car_km > 0
        assert summary.kwh_per_1000_ton_km > 0

    def test_regen_ratio(self):
        """再生回收率 = 再生 / 牵引。"""
        calc = EnergyCalculator(num_cars=1, drive_efficiency=1.0)

        # 牵引
        car = _make_car_report(velocity=10.0, tractive_force=80000.0)
        calc.step(_make_force_report([car], dt=1.0, step=1, timestamp=0.0))

        # 制动
        car.velocity = 20.0
        car.tractive_force = 0.0
        car.electric_brake_force = -56000.0
        calc.step(_make_force_report([car], dt=1.0, step=2, timestamp=1.0))

        summary = calc.summary()
        # 牵引 > 0 时回收率应 > 0
        assert summary.regen_ratio > 0.0

    def test_regen_ratio_zero_when_no_traction(self):
        """无牵引时回收率返回 0（避免 ZeroDivisionError）。"""
        summary = EnergyTripSummary(total_traction_kwh=0.0, total_regen_kwh=100.0)
        assert summary.regen_ratio == 0.0


# ═══════════════════════════════════════════════════════════════
# EnergyCalculator 参数与重置
# ═══════════════════════════════════════════════════════════════

class TestEnergyCalculatorParams:
    """参数调整与重置"""

    def test_set_load_level_updates_aux(self):
        calc = EnergyCalculator(num_cars=6, load_level=LoadLevel.AW2)
        assert calc.aux_power_total == 240_000

        calc.set_load_level(LoadLevel.AW3)
        assert calc.aux_power_total == 270_000  # 45kW × 6

        calc.set_load_level(LoadLevel.AW0)
        assert calc.aux_power_total == 180_000  # 30kW × 6

    def test_custom_aux_power(self):
        calc = EnergyCalculator(num_cars=4, aux_power_per_car=50_000)
        assert calc.aux_power_total == 200_000

    def test_set_drive_efficiency(self):
        calc = EnergyCalculator(drive_efficiency=0.85)
        calc.set_drive_efficiency(0.90)
        assert calc.drive_efficiency == 0.90

        calc.set_drive_efficiency(999)  # 应被截断到 1.0
        assert calc.drive_efficiency == 1.0

        calc.set_drive_efficiency(-0.5)  # 应被截断到 0.0
        assert calc.drive_efficiency == 0.0

    def test_reset_clears_accumulation(self):
        calc = EnergyCalculator(num_cars=1, drive_efficiency=1.0)
        car = _make_car_report(velocity=10.0, tractive_force=80000.0)

        calc.step(_make_force_report([car], dt=1.0))
        assert calc.traction_kwh > 0

        calc.reset()
        assert calc.traction_kwh == 0.0
        assert calc.regen_kwh == 0.0
        assert calc.aux_kwh == 0.0
        assert calc.net_kwh == 0.0
        assert calc.last_step is None
        assert len(calc.step_history) == 0


# ═══════════════════════════════════════════════════════════════
# EnergyTripSummary 字符串表示
# ═══════════════════════════════════════════════════════════════

class TestEnergyTripSummaryRepr:
    """汇总报告字符串"""

    def test_repr_includes_key_metrics(self):
        s = EnergyTripSummary(
            total_traction_kwh=100.0,
            total_regen_kwh=30.0,
            total_aux_kwh=20.0,
            net_kwh=90.0,
            trip_distance_m=5000.0,
            kwh_per_car_km=3.0,
        )
        r = repr(s)
        assert "100.00 kWh" in r
        assert "30.00 kWh" in r
        assert "30.0%" in r  # regen_ratio
        assert "90.00 kWh" in r
        assert "3.000 kWh/(车·km)" in r


# ═══════════════════════════════════════════════════════════════
# 集成测试：与 VehicleController 联用
# ═══════════════════════════════════════════════════════════════

class TestIntegrationWithController:
    """EnergyCalculator 与 VehicleController 集成"""

    def test_full_simulation_run(self):
        """完整仿真运行：启动→惰行→制动→停车，验证能耗合理性。"""
        from src.vehicle.vehicle_controller import VehicleController
        from src.vehicle.environment import MockEnvironment
        from src.common.consist import CONSIST_4M2T
        from src.common.track_position import MockTrackQuery

        track = MockTrackQuery()
        env = MockEnvironment()
        controller = VehicleController(CONSIST_4M2T, track, env)
        calc = EnergyCalculator(num_cars=6, load_level=LoadLevel.AW2, drive_efficiency=0.85)

        # ── 加速阶段（牵引） ──
        controller.set_throttle(1.0)
        for _ in range(200):
            report = controller.step(0.05)
            step = calc.step(report)
            if report.head_velocity > 15.0:
                break

        assert calc.traction_kwh > 0, "加速阶段应有牵引能耗"

        # ── 惰行阶段 ──
        controller.coast()
        for _ in range(50):
            report = controller.step(0.05)
            calc.step(report)

        # ── 制动阶段（全电制动） ──
        controller.set_brake(0.7)
        initial_speed = controller.head_speed
        for _ in range(300):
            report = controller.step(0.05)
            step = calc.step(report)
            if controller.is_stopped:
                break

        final_speed = controller.head_speed
        assert final_speed < 0.1, f"列车应停止: v={final_speed:.3f} m/s"

        # 制动阶段应有再生回收
        assert calc.regen_kwh > 0, "电制动应有再生回收"
        assert calc.regen_kwh < calc.traction_kwh, "回收量应 < 牵引量"

        # 辅助系统持续耗电
        total_time = report.timestamp
        expected_aux_min = 240_000 * total_time / 3_600_000 * 0.5  # 至少一半时间有电
        assert calc.aux_kwh > 0, "辅助系统应持续耗电"

        # 净电耗 = 牵引 - 再生 + 辅助
        expected_net = calc.traction_kwh - calc.regen_kwh + calc.aux_kwh
        assert calc.net_kwh == pytest.approx(expected_net, rel=1e-6)

        # 行程速度应在合理范围
        distance_m = report.head_position.offset  # 简化：MockTrack 无分支
        summary = calc.summary(
            distance_m=distance_m,
            total_mass_kg=controller.consist.total_mass,
        )
        assert summary.kwh_per_car_km > 0
        # 地铁单位能耗通常在 1-5 kWh/(车·km)
        assert summary.kwh_per_car_km < 10.0, \
            f"单位能耗异常高: {summary.kwh_per_car_km:.2f} kWh/(车·km)"

    def test_brake_to_zero_has_regen_then_stops(self):
        """验证制动到零期间：电制动转矩仍存在，但低速回收效率趋零。"""
        from src.vehicle.vehicle_controller import VehicleController
        from src.vehicle.environment import MockEnvironment
        from src.common.consist import CONSIST_4M2T
        from src.common.track_position import MockTrackQuery

        track = MockTrackQuery()
        env = MockEnvironment()
        controller = VehicleController(CONSIST_4M2T, track, env)

        # 先加速到一定速度（4M2T 满牵引约 0.9 m/s²，需 ~12s 到 10 m/s）
        controller.set_throttle(1.0)
        for _ in range(400):
            controller.step(0.05)

        assert controller.head_speed > 10.0, \
            f"加速后速度应 > 10 m/s, 实际: {controller.head_speed:.2f} m/s"

        # 全电制动
        controller.set_brake(0.7)
        calc = EnergyCalculator(num_cars=6, load_level=LoadLevel.AW2)

        high_speed_regen = 0.0
        low_speed_regen = 0.0
        step_count_high = 0
        step_count_low = 0

        for _ in range(500):
            report = controller.step(0.05)
            step = calc.step(report)

            if report.head_velocity > 1.0:
                high_speed_regen += step.regen_energy_j
                step_count_high += 1
            else:
                low_speed_regen += step.regen_energy_j
                step_count_low += 1

            if controller.is_stopped:
                break

        # 高速区应有显著回收
        assert high_speed_regen > 0, "高速区应有再生回收"

        # 低速区 (v < 1 m/s ≈ 3.6 km/h) 回收效率应极低
        if step_count_low > 0:
            avg_low_regen_per_step = low_speed_regen / step_count_low
            avg_high_regen_per_step = high_speed_regen / step_count_high
            # 低速区每步回收功率应远小于高速区
            assert avg_low_regen_per_step < avg_high_regen_per_step * 0.3, \
                f"低速回收未显著衰减: low={avg_low_regen_per_step:.1f}, high={avg_high_regen_per_step:.1f}"


# ═══════════════════════════════════════════════════════════════
# 直接运行
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
