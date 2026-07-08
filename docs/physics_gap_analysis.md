# 车辆仿真模块 — 深度物理需求缺口分析

> 基于五阶段物理需求清单的逐项审计结果。
> 审计日期：2026-07-08
> 审计范围：`src/vehicle/` + `src/common/` 全部文件

---

## 一、已闭环项（6/16）

| # | 需求 | 实现位置 |
|:--|:---|:---|
| 1 | 固定步长微积分循环 | `dynamics_pipeline.py:77-78` — DT_PHY_MAX=0.001s 子步切分 |
| 2 | Davis 基本阻力 | `forces.py:35-54` — R = A + Bv + Cv² |
| 3 | 坡道重力分量 | `forces.py:57-72` — F = -m·g·gradient/1000 |
| 4 | 惰行自然减速 | `VehicleController.coast()` + 阻力始终生效 |
| 5 | 天气→黏着系数映射 | `IEnvironmentQuery.get_adhesion_coefficient()` |
| 6 | 恒功率衰减曲线 | `forces.py:144-151` — 三段式牵引特性 |

---

## 二、待闭环项（8 项）

### 🔴 P0 — 现有 Bug

#### 1. 制动力 double-multiplication

**位置**: `forces.py:182-185`

```python
max_brake = (car.max_service_brake_force +
             (car.max_emergency_brake_force - car.max_service_brake_force) * brake_level)
magnitude = max_brake * brake_level
```

**问题**: `brake_level` 被乘了两次。第一次插值 service→emergency，第二次整体缩放。当 `brake_level=0.5` 时实际输出约为预期的 50%（非线性）。

**修复方向**: 去掉第二层乘法，或改为 `magnitude = max_brake`（因为 `max_brake` 已是插值结果）。

---

### 🔴 P1 — 阶段一缺口

#### 2. 回转质量系数

**位置**: `dynamics_pipeline.py:108`

```python
a = net_forces[i] / consist[i].mass
```

**缺失**: 未考虑旋转部件（轮对、电机转子）的转动惯量。真实列车等效质量约为静质量的 1.06~1.12 倍。

**修复方向**:
- `CarConfig` 增加 `rotary_mass_factor: float = 1.08` 字段
- Pipeline 中改为 `a = net_forces[i] / (consist[i].mass * consist[i].rotary_mass_factor)`

**工作量**: ~5 行代码

---

### 🔴 P2 — 阶段三缺口

#### 3. 空转/滑行警报

**位置**: `forces.py:156-158` (牵引), `forces.py:188-190` (制动)

**缺失**: 黏着超限时静默截断，无任何信号输出。

**修复方向**:
- `calc_tractive_force` 和 `calc_brake_force` 返回值改为 `(force, is_limited: bool)` 元组
- 或在 `ForceReport` 中增加 `traction_limited: bool` / `brake_limited: bool` 字段
- Pipeline 中汇总各车黏着状态，抛出"空转/滑行"事件供 UI 消费

**工作量**: ~30 行代码

---

### 🟡 P3 — 阶段四缺口

#### 4. AW0-AW3 载荷等级

**缺失**: 仅有注释"AW2 为基准"，无载荷枚举和切换接口。

**修复方向**:
- 定义 `LoadLevel` 枚举：AW0(空载) / AW1(满座) / AW2(定员) / AW3(超载)
- `CarConfig` 增加各等级对应的质量值和乘客质量
- `VehicleController` 增加 `set_load_level(level)` 方法

**参考数据** (B型地铁):
| 等级 | 描述 | 乘客质量增量 |
|:---|:---|:---|
| AW0 | 空车 | 0 t |
| AW1 | 满座 | ~8.4 t (140人×60kg) |
| AW2 | 定员 | ~14.4 t (240人×60kg) |
| AW3 | 超载 | ~21.6 t (360人×60kg) |

**工作量**: ~60 行代码

#### 5. 动态质量更新接口

**缺失**: `CarConfig.mass` 构造后不可变，无更新通路。

**修复方向**: 在 `VehicleController` 中暴露 `set_load_level()`，内部更新各节车的当前质量。

**工作量**: ~20 行代码

#### 6. 制动负荷补偿

**缺失**: 制动力不随质量变化。满载列车需要更大的制动力才能达到与空载相同的减速度。

**修复方向**: 在 `calc_brake_force` 中增加负荷补偿因子：
```python
load_compensation = car.mass / car.base_mass  # 基准为 AW2 质量
magnitude *= load_compensation
```

**工作量**: ~10 行代码

---

### 🟡 P4 — 阶段五缺口

#### 7. PT1 一阶低通滤波（作动迟滞）

**缺失**: 指令阶跃→力阶跃，零延迟。真实制动气缸充气和电机建磁存在 ~0.3-0.5s 的时间常数。

**修复方向**:
- 在 Pipeline 或 VehicleController 中增加滤波器状态
- 对 effective_throttle 和 effective_brake 分别施加 PT1 滤波：
  ```
  filtered = filtered + (target - filtered) * (1 - exp(-dt / tau))
  ```
- `tau_traction ≈ 0.3s`（电机建磁）, `tau_brake ≈ 0.5s`（气缸充气）

**工作量**: ~40 行代码

---

### 🟢 P5 — 阶段二/五缺口

#### 8. 曲线附加阻力

**缺失**: `ITrackQuery` 无曲线半径查询，`forces.py` 无曲线阻力计算。

**修复方向**:
- `ITrackQuery` 增加 `get_curve_radius(pos) -> Optional[float]`（返回曲线半径 m 或 None 表示直线）
- `forces.py` 增加 `calc_curve_resistance(car, radius) -> float`
- 经验公式: R_curve ≈ 700 / R × mass × g（R 为曲线半径，单位 m）

**工作量**: ~40 行代码

#### 9. 电空混合制动交接

**缺失**: 单一标量制动力，无电气/空气制动区分。

**修复方向**:
- 拆分 `calc_brake_force` 为 `calc_electric_brake` + `calc_friction_brake`
- 电气制动在低速 (< 5 km/h) 时按比例衰减
- 空气制动在电气制动衰减区自动补偿差值，维持总制动力不变
- 交接逻辑: `friction_brake = total_brake - electric_brake * fade_ratio(v)`

**工作量**: ~80 行代码

---

## 三、实施建议

建议分两个 Sprint 完成：

| Sprint | 内容 | 预计工作量 |
|:---|:---|:---|
| Sprint 1 | P0 Bug修复 + P1 回转质量 + P2 滑行警报 | ~45 行，低风险 |
| Sprint 2 | P3 载荷系统 + P4 PT1滤波 | ~130 行，需改接口 |
| Sprint 3（可选） | P5 曲线阻力 + 电空制动 | ~120 行，需改 ITrackQuery |
