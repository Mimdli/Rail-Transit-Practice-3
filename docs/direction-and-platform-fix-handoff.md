# 上下行方向 & 站台阻塞 & 折返换链 — 修复文档

## 问题现状（修复前）

- **1车（下行）**：在 seg5（下行链），待发状态，但 **seg1（上行链）也被 1车占用**
- **2车（上行）**：D→C 跑完后卡在 seg3（站C），状态=进路等待，报"没有可办理的线路区段"
- **折返后**：两车同时跑到同一条链上（如都到 UP 链），互相阻塞死锁

## 已完成的修复

### Bug 1（核心）：`TrackDataAdapter.from_absolute` 负位置兜底不尊重 `hint_seg_id`

**文件**：`src/track/adapter.py` `from_absolute()` 方法

**现象**：列车尾部车厢处于线路起点之前（绝对坐标为负值）。`from_absolute` 处理负位置时走兜底逻辑，找 `abs_start≈0` 的第一个段作为"根段"。演示线路中 seg1 和 seg5 的 `abs_start` 都是 0，但 seg1 排在 segments 列表前面，所以**总是返回 seg1**。即使传了 `hint_seg_id=5`，这段兜底代码也**完全忽略 hint_seg_id**。

**修复**：
1. 从 `_disambiguate_candidates` 提取 `_build_chain_ids(seed_seg_id)` 方法，双向遍历邻居构建同链段 ID 集合
2. Step 3 兜底改为：若有 `hint_seg_id`，先在同链上找 `abs_start≈0` 的根段；无 hint 才走原逻辑
3. `_disambiguate_candidates` 中的内联链构建代码改为调用 `_build_chain_ids()`

### Bug 1b：`TrackDataQuery.from_absolute` (VehicleUiQueryAdapter) 同样问题

**文件**：`src/vehicle/ui_adapter.py` `from_absolute()` 方法

**修复**：负位置兜底同样改为：有 `hint_seg_id` 时在同链上找根段，避免并行链歧义。

### Bug 2（连锁影响）：信号系统因 seg1 被占用而亮红灯

**文件**：`src/signal/system.py`

**现象**：因为 Bug 1 导致 1车的尾部车厢"污染"了 seg1，信号系统检测到 seg1 被占用后亮红灯，阻塞 2车。

**修复**：修复 Bug 1 后，seg1 不再被误占用，信号链恢复正常，此问题自动消失。

### Bug 3（独立）：信号保护中"目标站在信号之前"的方向判断

**文件**：`src/dispatch/dispatch_manager.py` `_apply_signal_protection()` 方法

**修复**：
1. 优先使用实际目标 `TrackPosition`（链感知）计算目标绝对坐标，而非直接用 `station.position`（可能在并行链中指错站台）
2. 增加显式的 `tgt_ahead > 0` 守卫条件（目标必须在行驶方向前方）
3. 新增绝对距离兜底比较，处理方向判断的边界情况

### Bug 4（关键）：折返后换链失败，`route_between` 混入两链条段导致死锁

**文件**：`src/dispatch/dispatch_manager.py`

**现象**：折返后列车需要从一条链切换到另一条链（如 1车完成 A→B→C→D 下行后，折返需要从 D→C→B→A 走另一条链）。但 `compute_station_route` 无法跨链寻路（两条链在拓扑上不相连），回退到 `route_between`，而 `route_between` 仅基于绝对坐标筛选段，在双链拓扑中会**同时返回两条链的段**，导致进路中混入另一链占用的段 → 联锁拒绝 → 两车死锁。

**修复（3 处改动）**：

1. **移除 `_try_compute_route` 中的 `route_between` 回退**（约第 726 行）
   - `route_between` 基于绝对坐标，在并行链拓扑中从原理上就无法区分链
   - 改为 `compute_station_route` 失败时直接返回空，由上层处理

2. **新增 `_resolve_alt_chain_start` 方法**（约第 740 行）
   - 折返换链时，使用 `plan_index`（交路中的当前站序号）而非物理段号来确定当前站
   - 避免因列车停在站前 3m 容差范围内（`ARRIVAL_TOLERANCE`）而误判车站
   - 在当前站找另一侧链、匹配新运行方向的站台作为换链起点

3. **修改 `_compute_leg_route_and_target`**（约第 690 行）
   - 当 `compute_station_route` 返回空时（起终点在不同链），调用 `_resolve_alt_chain_start` 找到换链起点
   - 将列车 `reset_to` 新链站台，重新从新起点计算进路

### 其他已完成的修复（之前已做，保留）

1. `TrackDataAdapter.advance_position` — 传 `hint_seg_id=pos.segment_id`
2. `MockTrackQuery.advance_position` — 同上
3. `VehicleUiQueryAdapter` — advance + from_absolute 链消歧
4. `AutoDriveController` — from_absolute 传 hint
5. `simulation.py` — 多处 from_absolute 传 hint，双向初始化列车
6. `dispatch/train_manager.py` — 初始放置时链消歧
7. `vehicle_controller.py` — reset_to_absolute 传 hint
8. Web/桌面 UI 中切换自动驾驶根据方向选查站方法

## 关键设计要点（供数据库数据修复参考）

### 1. 并行链的绝对坐标重叠

演示线路中两条链（UP链 seg1-4，DOWN链 seg5-8）的 `abs_start` 均为 0，导致两链条段在 `0~1000m` 区间内**绝对坐标完全重叠**。这是有意设计，与数据库线路的双链并行结构一致。

**影响**：任何**仅基于绝对坐标**的查询（如 `get_seg_id_at`、`route_between`）都会在两条链中各返回一个段，必须通过 `hint_seg_id` 消歧。

**已修复的消歧点**：
- `TrackDataAdapter.from_absolute()` — 所有调用处都传 `hint_seg_id`
- `TrackDataAdapter.advance_position()` — 自动传当前位置的 `segment_id`
- 动力学管线（`dynamics_pipeline.py`）— 每辆车的 `old_seg_id` 作为 hint

**可能仍需修复的消歧点**（数据库数据实现时注意）：
- `TrackData.get_seg_id_at(position)` — 无 hint 参数，总是返回第一个匹配段
- `InterlockingService.route_between()` — 已从 `_try_compute_route` 移除，但仍存在于 `interlocking.py` 中供其他调用方使用

### 2. `compute_station_route` 的链感知能力

此函数通过站台方向（"up"/"down"）和段邻接关系（`start_neighbor`/`end_neighbor`）寻路，**天然支持链隔离**——不同链的段在拓扑图中不相连，路径不会跨链。

**前提条件**：
- 站台 `direction` 字段必须正确设置（"up" 对应 direction<0，"down" 对应 direction>=0）
- 段邻接关系必须正确（`start_neighbor` 指向上一段，`end_neighbor` 指向下一段）
- 不同链之间不应有邻接关系（除非确实存在物理渡线）

### 3. 折返换链的 `plan_index` 依赖

`_resolve_alt_chain_start` 使用 `runtime.plan_index`（在 `_check_arrival` 中更新）来确定列车当前所在车站。这要求：
- `_check_arrival` 必须在正确的车站触发（依赖 `auto_drive.distance_to_target`）
- `plan_index` 必须在 `_check_arrival` 中正确更新
- `ARRIVAL_TOLERANCE = 3.0m` — 列车停在站前 3m 内即视为到站。如果站间距较小（<10m），可能出现误判

### 4. `reset_to` 与 `reset_states` 的 direction 差异

- `reset_states(seg_id, offset)` — 使用 `self.direction` 决定尾部车厢排列方向
- `reset_to(TrackPosition)` — **不使用** `self.direction`，尾部始终向低 offset 方向排列

换链时调用 `reset_to`，尾部车厢得到相对于头车的低 offset。若头车 offset 接近 0，尾部会出现负 offset，在下一次动力学步进中由 `from_absolute` 正确映射到上一段（同链）。

## 验证步骤

1. 切换到 demo 数据源
2. 1车（下行）发车 → 应沿 A→B→C→D 走下行链(seg5-8)
3. 2车（上行）切换自动驾驶 → 应沿 D→C→B→A 走上行链(seg1-4)
4. 两车同时经过"站B"时不应互相阻塞（不同链不同段号）
5. 1车到达 D 后折返 → 应换到 UP 链(seg4→seg3→seg2→seg1)
6. 2车到达 A 后折返 → 应换到 DOWN 链(seg5→seg6→seg7→seg8)
7. 两车连续多圈运行不应出现跨链占用（同一段被两车同时占用）

## 快照关键数据（2026-07-12，修复前）

```
1车: seg=5, pos=0, status=待发
2车: seg=3, pos=501.4, status=进路等待, blocked=没有可办理的线路区段
     phase=APPROACHING, target=None
Occupancy:
  seg 1: [1车]   ← BUG! 1车在下行链不应占用上行链的seg1
  seg 3: [2车]
  seg 5: [1车]
Locks: (empty)
Signals:
  S01(seg1,UP): RED   ← 因seg1被占用
  S02(seg1,UP): RED
  S03(seg2,UP): RED   ← 防护seg1
  S04(seg3,UP): YELLOW ← S03红灯的预告
```

## 修复后验证数据（2026-07-12，1000 步仿真）

```
1车: seg=2 pos=486.0 dir=-1 route=(4,3) target=3 (UP链, D→C)
2车: seg=7 pos=633.6 dir=1 route=(6,7,8) target=4 (DOWN链, C→D)
Occupancy: seg2→1车, seg3→1车, seg7→2车
阻塞事件: 0  跨链污染: 0  死锁: 0
```
