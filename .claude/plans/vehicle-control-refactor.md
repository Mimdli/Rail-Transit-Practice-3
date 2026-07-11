# 车辆控制重构方案（供下一个 Claude Code 接力）

---

## 一、已完成的工作

### 1.1 新增 `VehicleState` 枚举（`src/vehicle/enums.py`）

车辆运行状态机，7 个状态：

```
INIT → STOPPED → STARTING → MOVING → COASTING → BRAKING → EMERGENCY
         ↑          │          │          │           │
         └──────────┘          └──────────┘           │
         停止后自动            惰行中加制动             │
                                                      │
         任意状态 ───────────────────────→ EMERGENCY  │
         EMERGENCY → STOPPED（完全停止后自动确认）    │
```

`TractionBrakeController.update_vehicle_state()` 在每个仿真步后根据物理状态自动推进状态转换。

### 1.2 新增 `TractionBrakeController`（`src/vehicle/traction_controller.py`）

从 `VehicleController` 中提取的**纯粹牵引/制动执行器**。

```
职责：
  - 管理 throttle / brake_level / target_speed
  - PT1 滤波器协调（与 PerCarDynamicsPipeline 配合）
  - 离散控制级位映射（ControlLevel → throttle/brake）
  - 联锁约束执行（门-牵引联锁、紧急制动覆盖）
  - VehicleState 管理
  - 高层控制指令（发车/停车/紧急/惰行）
```

**核心 API：**

| 方法 | 功能 |
|------|------|
| `set_throttle(level)` | 连续牵引指令 (0~1) |
| `set_brake(level)` | 连续制动指令 (0~1) |
| `apply_control_level(ControlLevel)` | 离散级位 → 连续值映射 |
| `command_start()` | 发车：释放制动，INIT/STOPPED → STARTING |
| `command_stop(brake_level)` | 停车：切断牵引 + 制动，→ BRAKING |
| `command_emergency()` | 紧急：任何状态 → EMERGENCY |
| `command_coast()` | 惰行：无牵引无制动，→ COASTING |
| `get_effective_command(interlock)` | 根据联锁约束返回修正后的 (throttle, brake) |
| `update_vehicle_state(is_stopped, head_speed)` | 每步后自动推进状态机 |

**`TractionInterlock` 数据类：**
```python
@dataclass
class TractionInterlock:
    traction_permitted: bool = True      # 牵引是否授权（信号/联锁）
    emergency_brake_required: bool = False
    door_open: bool = False              # 门开时禁止牵引
```

### 1.3 重构 `VehicleController`（`src/vehicle/vehicle_controller.py`）

**变更：**
- 创建 `self._traction = TractionBrakeController(self.pipeline)` 实例
- 保留代理属性（`throttle` / `brake_level` / `set_throttle()` / `set_brake()` / `emergency_brake()` / `coast()` / `apply_control_level()`）—— 向后兼容
- `step()` 中使用 `TractionInterlock` + `get_effective_command()` 联锁评估
- 每步后调用 `update_vehicle_state()` 推进状态机

**新增 API：**

| 方法 | 功能 |
|------|------|
| `controller.traction` | 直接访问 `TractionBrakeController` 实例（推荐新代码使用） |
| `controller.vehicle_state` | 获取当前 `VehicleState` |
| `controller.start_moving(throttle_level)` | 高层发车指令（联锁检查 + 关门 + 释放制动 + 牵引） |
| `controller.command_stop(brake_level)` | 高层停车指令 |
| `controller.command_emergency()` | 高层紧急制动指令 |
| `controller.reset_to(TrackPosition, velocity)` | 重置到指定线路位置 |
| `controller.reset_to_station(index)` | 重置到指定车站 |
| `controller.reset_to_absolute(abs_m)` | 重置到绝对位置 (m) |
| `controller.snapshot()` → dict | 创建完整状态快照 |
| `controller.restore_snapshot(dict)` | 从快照恢复 |

### 1.4 UI 已有改动

| 文件 | 改动 |
|------|------|
| `src/ui/controls.py` | 新增"发车"按钮、"停车"按钮、"重置位置"面板（车站下拉+跳转按钮）、`populate_stations()` |
| `src/ui/dashboard.py` | 模式标签显示 `VehicleState`（停止/启动中/运行中/惰行/制动中/紧急） |
| `src/ui/main_window.py` | 初始化/切数据源时填充车站下拉框 |

### 1.5 测试状态

全部 57 项测试通过 ✓

---

## 二、当前架构的核心混淆

### 两个正交维度被混在一起

**维度 A：进路选择（走哪条路）** — 由 `Route` + `TrackDataAdapter.set_active_route()` 控制
- 自动进路：`Route.is_auto == True`（seg_ids 为空），系统沿主线自动算路
- 手动进路：`Route.is_auto == False`（有具体 seg_ids），用户选预定义路径

**维度 B：驾驶模式（谁控制油门/刹车）** — 由 `RunningMode` 枚举控制
- `MANUAL`：人通过 UI 按钮控制
- `AUTOMATIC`：`AutoDriveController` 自动控制

目前这两维度的管理全部寄生在 `AutoDriveController` 上，导致：

```
AutoDriveController 当前职责（4 件事混在一起）：
├── 进路管理：_route, _all_routes, set_route(), _compute_and_set_route()
├── 驾驶控制：step(), _step_driving(), _step_dwell(), _step_departing()
├── 目标管理：set_target(), distance_to_target
├── 停站状态机：station_phase, _dwell_timer
```

### 混淆的具体表现

1. **进路数据存在 `AutoDriveController` 上** — 语义不符。手动驾驶时也需要进路。但当前要设进路必须调 `auto_drive.set_route()`。

2. **"自动进路" vs "自动驾驶"** — UI 中：
   - 进路下拉框选项："自动（系统算路）" → 指自动算进路
   - 模式按钮："自动" → 指自动驾驶
   - 两个"自动"含义不同，但都操作 `AutoDriveController`

3. **切驾驶模式时强制捆绑寻路** — `_on_auto_mode()` 同时做：找下一站 → 设目标（触发算路）→ 切 AUTOMATIC

4. **四种合理组合只支持两种**：

|  | 自动进路 | 手动进路 |
|--|---------|---------|
| **自动驾驶** | ✅ 当前支持 | ❌ 不支持（ATO 但按调度指定路径走侧线） |
| **手动驾驶** | ❌ 不支持（人开车但系统自动算主线） | ✅ 当前支持 |

---

## 三、待完成的整理工作

### 3.1 目标架构

```
                 ┌──────────────────┐
                 │   进路管理模块    │  ← 独立，只管"走哪条路"
                 │  RouteManager    │
                 │  - active_route  │
                 │  - auto/compute  │
                 │  - manual/select │
                 └────────┬─────────┘
                          │ 提供路径信息
                          ▼
┌─────────────────┐  ┌──────────────────┐
│  驾驶模式管理    │  │  牵引/制动执行器  │
│  DrivingMode    │  │  TractionBrake   │
│  MANUAL/AUTO    │  │  Controller      │
└────────┬────────┘  └────────┬─────────┘
         │                    │
         ▼                    ▼
  ┌──────────────────────────────────┐
  │       VehicleController          │
  │  (物理仿真 + 状态)                │
  └──────────────────────────────────┘
```

### 3.2 待完成的任务（按优先级）

#### Task 1：从 `AutoDriveController` 中剥离进路管理 `src/vehicle/route_manager.py`

新建 `RouteManager` 类，从 `AutoDriveController` 中迁移：

```python
class RouteManager:
    """列车进路管理器 —— 独立于驾驶模式。
    
    职责：
      1. 存储所有可用进路
      2. 管理当前活动进路
      3. 自动算路（沿主线 forward neighbor 链）
      4. 同步活动进路到 TrackDataAdapter
    """
    
    def __init__(self, track_adapter):
        self._track_adapter = track_adapter
        self._all_routes: List[Route] = []
        self._active_route: Optional[Route] = None
    
    # 迁移自 AutoDriveController 的方法：
    # set_route(), set_available_routes(), 
    # _compute_and_set_route()
    
    # 新增：
    def compute_route_to(target_position) -> Optional[Route]
    def compute_route_to_station(station_index) -> Optional[Route]
```

**影响文件：**
- 新建 `src/vehicle/route_manager.py`
- `AutoDriveController`：删除 `_route`、`_all_routes`、`set_route()`、`set_available_routes()`、`_compute_and_set_route()`，改为持有 `RouteManager` 引用
- `MainWindow._init_modules()`：`RouteManager` 在此创建，传给 `AutoDriveController` 和 `ControlPanel`
- `ControlPanel`：进路下拉框绑定到 `RouteManager` 而不是 `AutoDriveController`

#### Task 2：拆分 UI 控制面板 `src/ui/controls.py`

重组为四个独立功能组，标签明确区分：

```
┌─ 运行控制 ──────────────────────────┐
│  [发车]    [停车]    [紧急制动]       │
│  (驾驶模式: ●手动 ○自动)    [切换]   │
└─────────────────────────────────────┘

┌─ 牵引/制动手柄（司控器） ───────────┐
│   P3 全牵引  [●] [ ] [ ]           │
│   P2 中牵引  [ ] [●] [ ]           │
│   P1 低牵引  [ ] [ ] [●]           │
│   COAST 惰行      [COAST]          │
│   B1 常用制动     [B1]             │
│   B2 全制动       [B2]             │
│   EB 紧急制动     [EB]             │
│   当前级位: P2 中牵引               │
└────────────────────────────────────┘

┌─ 进路选择 ──────────────────────────┐
│  路线: [自动（系统算路）▾]           │
│  (与驾驶模式无关，手动/自动下均可用) │
└─────────────────────────────────────┘

┌─ 车门控制 ──────────────────────────┐
│  [开左门]    [开右门]    [关门]      │
└─────────────────────────────────────┘
```

**改动要点：**
- 手柄区使用互斥按钮组（`QButtonGroup`，模拟真实司控器），每级是一个 `apply_control_level(ControlLevel.XXX)`
- 驾驶模式切换独立按钮，不影响进路选择
- 进路下拉框在手动驾驶模式下也可用（改路线不影响驾驶模式）
- `_on_auto_mode()` 不再捆绑寻路——寻路由进路管理独立处理

#### Task 3：拆分 `MainWindow` 中的模式切换逻辑 `src/ui/main_window.py`

**当前问题位置：**

| 位置 | 问题 |
|------|------|
| `_on_track_clicked()` L837 | 点击线路图 → 强制切 `AUTOMATIC`，应只设目标+进路，不强制改模式 |
| `_on_fast_forward()` L501 | 跳到下一站 → 强制切 `AUTOMATIC`，应改为"发车到下一站" |
| `_update()` L415 | `if AUTOMATIC: auto_drive.step()` — 保持不变，这是正确的 |
| `_replace_track()` L224 | 直接 `controller.running_mode = MANUAL`，应通过统一入口 |

**建议：**
- 创建 `_set_driving_mode(mode)` 统一入口，处理模式切换的所有副作用（关门、制动、重置 ATO 状态等）
- `_on_track_clicked` 只设目标和进路，**不**切换驾驶模式
- `_on_fast_forward` 改为调用 `start_moving()` + 自动寻路，由用户决定用哪种模式

#### Task 4：UI 显示 `vehicle_state` 并区分进路/驾驶 `src/ui/dashboard.py`

在仪表盘上清晰展示三个独立状态：

```
模式: 手动 · 运行中         ← 驾驶模式 + VehicleState
进路: 自动（系统算路）      ← 进路模式（独立于驾驶模式）
```

新增一行显示进路信息（从 `RouteManager` 获取）。

#### Task 5：为 `TractionBrakeController` 添加手动模式门禁

当前 `set_throttle()` / `set_brake()` 无条件执行。需要添加模式检查，使得在 AUTOMATIC 模式下：
- UI 按钮直接调用的 `set_throttle/set_brake` 被忽略
- 只有 `AutoDriveController` 能设置（或者 ATO 通过一个内部旁路接口设置）

**方案：**
```python
class TractionBrakeController:
    _driving_mode: RunningMode = RunningMode.MANUAL
    
    def set_throttle(self, level, *, bypass_mode_check=False):
        if not bypass_mode_check and self._driving_mode != RunningMode.MANUAL:
            return  # 非手动模式，忽略外部 throttle 指令
        self.throttle = max(0.0, min(1.0, level))
        ...
    
    def _set_throttle_internal(self, level):
        """供 AutoDriveController 使用的内部旁路"""
        self.set_throttle(level, bypass_mode_check=True)
```

**影响文件：**
- `src/vehicle/traction_controller.py`：添加 `_driving_mode` + `bypass_mode_check`
- `src/vehicle/auto_drive.py`：改用 `_set_throttle_internal()`
- `VehicleController.set_running_mode()`：同步到 `_traction._driving_mode`

---

## 四、不改动的部分

以下模块/逻辑保持不变：

- `VehicleController` 的物理仿真层（`step()`、`_build_report()`、编组管理）
- `PerCarDynamicsPipeline` 动力学管线
- `ForceReport` / `CarForceReport` 力学报告
- `MockInterlock` 联锁 Mock
- 车门控制逻辑
- 载荷管理逻辑
- 信号/供电/日志模块

---

## 五、实施顺序建议

```
Phase A (独立)  → Task 1：新建 RouteManager，从 AutoDriveController 剥离
Phase B (独立)  → Task 5：TractionBrakeController 添加模式门禁
Phase C (依赖A) → Task 2：UI 面板重组（进路 + 手柄 + 运行控制分离）
Phase D (依赖A) → Task 4：Dashboard 显示进路状态
Phase E (依赖C) → Task 3：MainWindow 统一模式切换入口
```

Phase A 和 B 互不依赖，可以并行。

---

## 六、关键文件路径

| 文件 | 说明 |
|------|------|
| `src/vehicle/vehicle_controller.py` | 顶层控制器（物理仿真+状态）— 已重构 |
| `src/vehicle/traction_controller.py` | 牵引/制动执行器 — **新建**，已完成 |
| `src/vehicle/auto_drive.py` | 自动驾驶控制器 — 待精简（剥离进路管理） |
| `src/vehicle/enums.py` | 所有枚举（VehicleState, RunningMode, ControlLevel 等） |
| `src/track/route.py` | Route 数据类 + 自动算路函数 |
| `src/track/adapter.py` | TrackDataAdapter（含活动进路） |
| `src/ui/controls.py` | 控制面板 — 部分改动，待进一步重组 |
| `src/ui/dashboard.py` | 仪表盘 — 部分改动，待加进路显示 |
| `src/ui/main_window.py` | 主窗口 — 待统一模式切换入口 |
| `tests/test_controller.py` | VehicleController 测试 — 全部通过 |
| `tests/test_auto_drive.py` | AutoDriveController 测试 — 全部通过 |
