# 轨道交通模拟系统 — 接口文档

> 记录项目各模块已实现的公共接口，供开发参考。

---

## 数据类定义

### `src/track/data.py` — 轨道线路数据

| 类 | 字段 | 说明 |
|------|------|------|
| `Station` | `station_id`, `name`, `position`, `platform_ids` | 车站 |
| `Platform` | `platform_id`, `position`, `seg_id`, `direction`, `station_name` | 站台 |
| `Segment` | `seg_id`, `length`, `start_neighbor`, `end_neighbor`, `start_lateral`, `end_lateral`, `abs_start` | 线路区段 |
| `SpeedLimit` | `seg_id`, `start_offset`, `end_offset`, `speed_limit`, `abs_start`, `abs_end` | 限速区段 |
| `Gradient` | `seg_id`, `start_offset`, `end_offset`, `gradient`, `direction`, `abs_start`, `abs_end` | 坡度区段 |

### `src/vehicle/model.py` — 车辆模型

| 枚举 | 值 |
|------|------|
| `ControlLevel` | `EMERGENCY_BRAKE`(-3) ~ `FULL_TRACTION`(3) |
| `DoorSide` | `NONE`, `LEFT`, `RIGHT` |
| `RunningMode` | `MANUAL`, `AUTOMATIC` |

### `src/signal/system.py` — 信号系统

| 枚举 | 值 |
|------|------|
| `SignalAspect` | `GREEN`, `YELLOW`, `RED` |

### `src/power/supply.py` — 供电状态

| 枚举 | 值 |
|------|------|
| `PowerStatus` | `NORMAL`, `LOW_VOLTAGE`, `POWER_OFF`, `RECOVERING` |

---

## 已实现接口（轨道数据阶段）

### `TrackData` (src/track/data.py)

#### 数据构建

| 方法 | 说明 |
|------|------|
| `build_coordinates()` | BFS 图遍历，为所有 Segment 计算 `abs_start`，并映射限速/坡度的绝对坐标 |

#### 位置查询

| 方法 | 返回 | 说明 |
|------|------|------|
| `get_speed_limit_at(position) -> float` | m/s | 查询指定位置 (m) 的限速值，默认 22.0 |
| `get_gradient_at(position) -> float` | ‰ | 查询指定位置 (m) 的坡度值，默认 0.0 |
| `get_station_at(position, threshold=50) -> Station\|None` | — | 查询距离 position 最近（< threshold m）的车站 |
| `get_nearest_station_ahead(position) -> Station\|None` | — | 查询 position 前方最近的车站 |
| `get_platform_side_at(position) -> str` | `"left"`/`"right"`/`""` | 查询指定位置的站台侧 |
| `get_seg_id_at(position) -> int` | Seg ID | 查询指定位置所在的 Seg 编号 |
| `total_length() -> float` | m | 线路总长（含分支） |

### `TrackLoader` (src/track/loader.py)

| 方法 | 说明 |
|------|------|
| `load_from_excel(file_path) -> TrackData` | 从真实 xls 文件加载，解析 5 个 Sheet |
| `load_demo_data() -> TrackData` | 加载内置 5 站 4 区间演示数据（不依赖 Excel） |

---

## 骨架接口（待实现）

以下接口仅定义类和方法签名，主体方法均 `raise NotImplementedError`。

### `VehicleModel` (src/vehicle/model.py)

| 方法 | 说明 |
|------|------|
| `apply_traction(level)` | 设置牵引/制动级位 |
| `set_control_level_direct(level)` | 直接设置级位（自动模式） |
| `open_door(side) -> bool` | 开门 |
| `close_door()` | 关门 |
| `doors_closed() -> bool` | 检查车门是否全关 |
| `step()` | 推进一个仿真步长 |
| `get_speed_kmh() -> float` | 获取速度 (km/h) |
| `reset()` | 重置车辆状态 |

### `ManualController` (src/vehicle/controller.py)

| 方法 | 说明 |
|------|------|
| `set_traction()` | 牵引 |
| `set_coast()` | 惰行 |
| `set_service_brake()` | 常用制动 |
| `set_full_brake()` | 全制动 |
| `set_emergency_brake()` | 紧急制动 |
| `open_left_door()` / `open_right_door()` | 开门 |
| `close_door()` | 关门 |

### `AutoController` (src/vehicle/controller.py)

| 方法 | 说明 |
|------|------|
| `set_target(position)` | 设置目标停车位置 |
| `step()` | 自动控制步进 |
| `is_stopped() -> bool` | 判断是否已停车 |

### `SignalSystem` (src/signal/system.py)

| 方法 | 说明 |
|------|------|
| `get_aspect_at(position, signals)` | 获取信号状态 |
| `check_red_signal_ahead(position, signals, look_ahead) -> bool` | 检查前方红灯 |
| `get_effective_speed_limit(position, track_limit, signals) -> float` | 综合限速 |

### `PowerSupply` (src/power/supply.py)

| 方法 | 说明 |
|------|------|
| `set_status(status)` | 设置供电状态 |
| `get_traction_limit() -> float` | 牵引能力系数 |
| `step(dt)` | 步进更新 |
| `can_traction() -> bool` | 是否允许牵引 |
| `reset()` | 重置 |

### `DoorInterlock` (src/door/interlock.py)

| 方法 | 说明 |
|------|------|
| `can_open_door()` | 是否允许开门 |
| `get_allowed_door_side() -> DoorSide` | 允许开门的侧 |
| `can_depart()` | 是否允许发车 |
| `is_at_platform() -> bool` | 是否在站台范围 |

### `Recorder` (src/logger/recorder.py)

| 方法 | 说明 |
|------|------|
| `start()` | 开始记录 |
| `record(type, desc, pos, speed)` | 记录事件 |
| `step(dt)` | 更新时间 |
| `get_events_by_type(type) -> list` | 按类型筛选 |
| `get_summary() -> dict` | 运行摘要 |
| `clear()` | 清除记录 |

### `Evaluator` (src/logger/evaluator.py)

| 方法 | 说明 |
|------|------|
| `update_max_speed(speed)` | 更新最高速度 |
| `record_stop(target, actual)` | 记录停车误差 |
| `evaluate(recorder) -> dict` | 综合评价 |
| `reset()` | 重置 |

### `MainWindow` (src/ui/main_window.py)

| 方法 | 说明 |
|------|------|
| `_init_modules()` | 初始化核心模块 |
| `_init_ui()` | 初始化界面 |
| `_update()` | 定时更新（100ms） |

### `Dashboard` (src/ui/dashboard.py)

| 方法 | 说明 |
|------|------|
| `refresh()` | 刷新状态显示 |

### `ControlPanel` (src/ui/controls.py)

| 方法 | 说明 |
|------|------|
| `update_log(recorder)` | 更新日志显示 |
