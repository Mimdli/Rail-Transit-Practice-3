"""EnergyCalculator — 能耗计算模块

基于 ForceReport 的力学数据，计算列车运行中的各项电能消耗与再生回收。

物理模型：
  牵引电耗  = tractive_force × v × dt / η_drive
  再生回收  = electric_brake_force × v × dt × η_regen(v)
  摩擦热损  = friction_brake_force × v × dt（纯热，不回收）
  辅助电耗  = P_aux × dt

净电耗 = 牵引 - 再生回收 + 辅助

再生效率 η_regen(v) 独立于制动力建模：
  - 0-3 km/h：反接制动区，反电动势不足，回收效率 = 0
  - 3-10 km/h：过渡区，效率线性上升 0→85%
  - 10+ km/h：正常再生区，效率 85→90%（渐近）
"""

from dataclasses import dataclass, field
from typing import List, Optional

from src.vehicle.force_report import ForceReport
from src.vehicle.enums import LoadLevel


# ═══════════════════════════════════════════════════════════════
# 再生制动效率曲线
# ═══════════════════════════════════════════════════════════════

def calc_regen_efficiency(velocity: float) -> float:
    """计算当前速度下的再生制动能量回收效率 η_regen(v)。

    物理机制：
    - 高速：电机反电动势足够高 → 电能回馈电网
    - 中速：反电动势下降 → 回收效率逐步降低
    - 低速 (< 3 km/h)：反电动势过低，电能无法回馈，
      制动转矩仍在但能量消耗在绕组电阻上（反接制动）
    - 0 km/h：功率为零（P = F × 0 = 0），效率无定义，返回 0

    Args:
        velocity: 当前速度 (m/s)，取绝对值。

    Returns:
        再生效率 (0.0 ~ 0.90)，无量纲。
    """
    v_kmh = abs(velocity) * 3.6

    if v_kmh < 3.0:
        # 反接制动区：反电动势不足以回馈电网
        return 0.0
    elif v_kmh < 10.0:
        # 过渡区：效率从 0 线性上升到 0.85
        return (v_kmh - 3.0) / 7.0 * 0.85
    else:
        # 正常再生区：效率 0.85 → 0.90（随速度渐近）
        extra = (v_kmh - 10.0) / 60.0 * 0.05
        return min(0.85 + extra, 0.90)


# ═══════════════════════════════════════════════════════════════
# 辅助系统功率参数
# ═══════════════════════════════════════════════════════════════

# 单节车辅助功率基准 (W)，随载荷等级递增（乘客越多 → HVAC 负荷越大）
_AUX_POWER_PER_CAR_MAP = {
    LoadLevel.AW0: 30_000,   # 空载：30 kW/车（基本通风+照明+电子设备）
    LoadLevel.AW1: 35_000,   # 满座：35 kW/车
    LoadLevel.AW2: 40_000,   # 定员：40 kW/车（设计基准）
    LoadLevel.AW3: 45_000,   # 超载：45 kW/车（最大制冷负荷）
}


# ═══════════════════════════════════════════════════════════════
# 能耗步骤报告
# ═══════════════════════════════════════════════════════════════

@dataclass
class EnergyStepReport:
    """单步能耗报告。

    所有能量单位为焦耳 (J)。正值 = 消耗电能，负值 = 回馈电能。
    """
    step: int = 0
    timestamp: float = 0.0              # 仿真时间 (s)
    dt: float = 0.0                     # 步长 (s)
    velocity: float = 0.0               # 头车速度 (m/s)

    traction_energy_j: float = 0.0      # 牵引电耗（电网侧）
    regen_energy_j: float = 0.0         # 再生回收（回馈电网，正数 = 回收量）
    friction_brake_loss_j: float = 0.0  # 摩擦制动热损（不可回收）
    aux_energy_j: float = 0.0           # 辅助系统电耗

    @property
    def net_energy_j(self) -> float:
        """净电耗 (J)。"""
        return self.traction_energy_j - self.regen_energy_j + self.aux_energy_j

    @property
    def traction_power_kw(self) -> float:
        """瞬时牵引功率 (kW)。"""
        return self.traction_energy_j / self.dt / 1000 if self.dt > 0 else 0.0

    @property
    def regen_power_kw(self) -> float:
        """瞬时再生功率 (kW)。"""
        return self.regen_energy_j / self.dt / 1000 if self.dt > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# 行程能耗汇总
# ═══════════════════════════════════════════════════════════════

@dataclass
class EnergyTripSummary:
    """一趟行程的能耗汇总。

    所有能量单位为千瓦时 (kWh)，更适合运营评估。
    """
    total_traction_kwh: float = 0.0        # 总牵引电耗
    total_regen_kwh: float = 0.0           # 总再生回收
    total_regen_net_kwh: float = 0.0       # 净再生回收（扣除回馈损耗后的实际回收）
    total_friction_brake_loss_kwh: float = 0.0  # 摩擦制动热损
    total_aux_kwh: float = 0.0             # 总辅助电耗

    net_kwh: float = 0.0                   # 净电耗

    trip_time_s: float = 0.0               # 行程时间
    trip_distance_m: float = 0.0           # 行程距离
    n_steps: int = 0                       # 仿真步数

    # 单位能耗指标
    kwh_per_car_km: float = 0.0           # kWh/(车·km)
    kwh_per_1000_ton_km: float = 0.0      # kWh/(千吨·km)

    @property
    def regen_ratio(self) -> float:
        """再生能量回收率 = 再生回收 / 牵引电耗。"""
        return self.total_regen_kwh / self.total_traction_kwh if self.total_traction_kwh > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"EnergyTripSummary(\n"
            f"  行程: {self.trip_distance_m:.0f} m, {self.trip_time_s:.1f} s, {self.n_steps} steps\n"
            f"  牵引: {self.total_traction_kwh:.2f} kWh\n"
            f"  再生: {self.total_regen_kwh:.2f} kWh (回收率 {self.regen_ratio:.1%})\n"
            f"  摩擦热损: {self.total_friction_brake_loss_kwh:.2f} kWh\n"
            f"  辅助: {self.total_aux_kwh:.2f} kWh\n"
            f"  净电耗: {self.net_kwh:.2f} kWh\n"
            f"  单位电耗: {self.kwh_per_car_km:.3f} kWh/(车·km), "
            f"{self.kwh_per_1000_ton_km:.2f} kWh/(千吨·km)\n"
            f")"
        )


# ═══════════════════════════════════════════════════════════════
# EnergyCalculator
# ═══════════════════════════════════════════════════════════════

class EnergyCalculator:
    """列车运行能耗计算器。

    逐步接收 ForceReport，累积各项能耗数据。

    典型用法:

        calc = EnergyCalculator(num_cars=6, load_level=LoadLevel.AW2)
        for report in controller.history:
            step = calc.step(report)
        trip = calc.summary(distance_m=3000, total_mass_kg=360000)
        print(trip)
    """

    def __init__(self,
                 num_cars: int = 6,
                 load_level: LoadLevel = LoadLevel.AW2,
                 drive_efficiency: float = 0.85,
                 aux_power_per_car: Optional[float] = None):
        """
        Args:
            num_cars: 编组车辆数，用于计算总辅助功率。
            load_level: 载荷等级，影响辅助功率基准。
            drive_efficiency: 驱动系统综合效率（电机+逆变器+齿轮）。
                              电网侧电能 → 轮周机械能的效率。
                              默认 0.85（即 85%）。
            aux_power_per_car: 覆盖默认的单车辅助功率 (W)。
                               None 则根据 load_level 自动查表。
        """
        self.num_cars = num_cars
        self.load_level = load_level
        self.drive_efficiency = drive_efficiency

        if aux_power_per_car is not None:
            self._aux_power_per_car = aux_power_per_car
        else:
            self._aux_power_per_car = _AUX_POWER_PER_CAR_MAP.get(
                load_level, _AUX_POWER_PER_CAR_MAP[LoadLevel.AW2]
            )

        # 辅助系统总功率 (W)
        self.aux_power_total = self._aux_power_per_car * num_cars

        # 累积量 (J)
        self._traction_j: float = 0.0
        self._regen_j: float = 0.0
        self._friction_brake_loss_j: float = 0.0
        self._aux_j: float = 0.0

        self._n_steps: int = 0
        self._last_step: Optional[EnergyStepReport] = None

        # 步骤历史
        self._step_history: List[EnergyStepReport] = []

    # ── 参数调整 ─────────────────────────────────────────────────

    def set_load_level(self, level: LoadLevel):
        """更改载荷等级（同步更新辅助功率）。"""
        self.load_level = level
        self._aux_power_per_car = _AUX_POWER_PER_CAR_MAP.get(
            level, _AUX_POWER_PER_CAR_MAP[LoadLevel.AW2]
        )
        self.aux_power_total = self._aux_power_per_car * self.num_cars

    def set_drive_efficiency(self, eta: float):
        """更改驱动系统效率。"""
        self.drive_efficiency = max(0.0, min(1.0, eta))

    # ── 仿真步进 ─────────────────────────────────────────────────

    def step(self, report: ForceReport) -> EnergyStepReport:
        """处理一个仿真步的 ForceReport，返回本步能耗。

        从整车视角求和所有车辆的能量：
          - 牵引能耗 = Σ( tractive_force_i × v_i × dt ) / η_drive
          - 再生回收 = Σ( electric_brake_force_i × v_i × dt ) × η_regen(v_i)
          - 摩擦热损 = Σ( friction_brake_force_i × v_i × dt )
          - 辅助电耗 = P_aux_total × dt

        Args:
            report: 单步的 ForceReport。

        Returns:
            EnergyStepReport: 本步能耗明细。
        """
        dt = report.dt
        head_v = abs(report.head_velocity)

        traction_j = 0.0
        regen_j = 0.0
        friction_loss_j = 0.0

        for car in report.cars:
            v = abs(car.velocity)

            # 轮周机械功率 × dt = 能量 (J)
            mech_traction = abs(car.tractive_force) * v * dt
            mech_elec_brake = abs(car.electric_brake_force) * v * dt
            mech_friction_brake = abs(car.friction_brake_force) * v * dt

            # 牵引：机械能 → 电网侧电能（除以驱动效率）
            traction_j += mech_traction / self.drive_efficiency

            # 再生：机械能 → 回馈电能（乘以再生效率）
            eta_regen = calc_regen_efficiency(car.velocity)
            regen_j += mech_elec_brake * eta_regen

            # 摩擦制动：纯热损，不回收
            friction_loss_j += mech_friction_brake

        # 辅助系统（与运动状态无关）
        aux_j = self.aux_power_total * dt

        # 更新累积量
        self._traction_j += traction_j
        self._regen_j += regen_j
        self._friction_brake_loss_j += friction_loss_j
        self._aux_j += aux_j
        self._n_steps += 1

        step_report = EnergyStepReport(
            step=report.step,
            timestamp=report.timestamp,
            dt=dt,
            velocity=head_v,
            traction_energy_j=traction_j,
            regen_energy_j=regen_j,
            friction_brake_loss_j=friction_loss_j,
            aux_energy_j=aux_j,
        )
        self._last_step = step_report
        self._step_history.append(step_report)
        return step_report

    # ── 汇总 ─────────────────────────────────────────────────────

    def summary(self,
                distance_m: float = 0.0,
                total_mass_kg: float = 0.0) -> EnergyTripSummary:
        """生成行程能耗汇总。

        Args:
            distance_m: 行程总距离 (m)，用于计算单位距离能耗。
            total_mass_kg: 列车总质量 (kg)，用于计算单位质量能耗。
                          可从 TrainConsist.total_mass 获取。

        Returns:
            EnergyTripSummary: 汇总报告，单位 kWh。
        """
        traction_kwh = self._traction_j / 3_600_000
        regen_kwh = self._regen_j / 3_600_000
        friction_kwh = self._friction_brake_loss_j / 3_600_000
        aux_kwh = self._aux_j / 3_600_000
        net_kwh = traction_kwh - regen_kwh + aux_kwh

        trip_time = self._last_step.timestamp if self._last_step else 0.0

        # 单位能耗指标
        car_km = distance_m * self.num_cars / 1000.0 if distance_m > 0 else 0.0
        kwh_per_car_km = net_kwh / car_km if car_km > 0 else 0.0

        ton_km = distance_m * total_mass_kg / 1_000_000.0 if distance_m > 0 and total_mass_kg > 0 else 0.0
        kwh_per_1000_ton_km = net_kwh / ton_km if ton_km > 0 else 0.0

        return EnergyTripSummary(
            total_traction_kwh=traction_kwh,
            total_regen_kwh=regen_kwh,
            total_regen_net_kwh=regen_kwh,  # 当前版本净回收 = 总回收（效率已计入）
            total_friction_brake_loss_kwh=friction_kwh,
            total_aux_kwh=aux_kwh,
            net_kwh=net_kwh,
            trip_time_s=trip_time,
            trip_distance_m=distance_m,
            n_steps=self._n_steps,
            kwh_per_car_km=kwh_per_car_km,
            kwh_per_1000_ton_km=kwh_per_1000_ton_km,
        )

    # ── 重置 ─────────────────────────────────────────────────────

    def reset(self):
        """重置累积数据，开始新一轮行程。"""
        self._traction_j = 0.0
        self._regen_j = 0.0
        self._friction_brake_loss_j = 0.0
        self._aux_j = 0.0
        self._n_steps = 0
        self._last_step = None
        self._step_history.clear()

    # ── 只读属性 ─────────────────────────────────────────────────

    @property
    def traction_kwh(self) -> float:
        """累积牵引电耗 (kWh)。"""
        return self._traction_j / 3_600_000

    @property
    def regen_kwh(self) -> float:
        """累积再生回收 (kWh)。"""
        return self._regen_j / 3_600_000

    @property
    def aux_kwh(self) -> float:
        """累积辅助电耗 (kWh)。"""
        return self._aux_j / 3_600_000

    @property
    def net_kwh(self) -> float:
        """累积净电耗 (kWh)。"""
        return self.traction_kwh - self.regen_kwh + self.aux_kwh

    @property
    def step_history(self) -> List[EnergyStepReport]:
        """返回逐步能耗历史。"""
        return list(self._step_history)

    @property
    def last_step(self) -> Optional[EnergyStepReport]:
        """最近一步的能耗报告。"""
        return self._last_step
