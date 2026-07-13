# 数据库线路数据问题 — 接力开发文档

> 创建日期：2026-07-13 | 分支：`vehicle` | 上游文档：[[direction-and-platform-fix-handoff]]

## 背景

[方向&站台修复文档](direction-and-platform-fix-handoff.md) 描述了 demo 双链线路（UP链 seg1-4, DOWN链 seg5-8）中因"绝对坐标重叠 + 查询无链感知"导致的 5 类 bug 及修复。本接力文档聚焦**数据库线路**（`data/railway.db`，319 segments，44 条根链，13 个车站，含道岔/渡线/车辆段）中是否存在同类问题。

核心矛盾：`TrackData.build_coordinates()` 为每条根链独立分配 `abs_start=0.0`，导致 44 条链的绝对坐标全线重叠（0~19000m）。任何仅基于绝对位置的查询都不可靠。

## 本次已完成（2026-07-13）

以下 6 项修改已提交在 `vehicle` 分支的工作树中（未 commit）：

### 1. `TrackData.get_seg_id_at` 增加 `hint_seg_id` 参数
**文件**: `src/track/data.py:295-330`
- 签名改为 `get_seg_id_at(self, position: float, hint_seg_id: int = 0) -> int`
- 有 hint 时先构建同链 ID 集合（`get_chain_ids`），在同链段中匹配
- 无 hint 或同链无匹配：回退到原有全量扫描（向后兼容）
- 新增 `get_chain_ids(seed_seg_id)` 公共方法，双向遍历 start_neighbor/end_neighbor 构建链段集合

### 2. `compute_mainline_route_to_station` 用站台 seg_id 消歧
**文件**: `src/track/route.py:162-177`
- 不再使用 `get_seg_id_at(station.position)`（44 条链中总是返回第一个匹配段）
- 改为遍历 `station.platform_ids` → 取关联站台的 `seg_id` 直接作为 target

### 3. 删除死代码 `InterlockingService.route_between`
**文件**: `src/dispatch/interlocking.py:89-106`（已删除）
- 全代码库 0 个调用方，仅 dispatch_manager 注释中提及
- 此方法基于绝对坐标筛选段，在多链拓扑中从原理上无法正确工作

### 4. `get_gradient_at` 回退逻辑增加同 seg_id 优先
**文件**: `src/track/data.py:245-258`
- 修改前：第一轮 seg_id+position 匹配失败 → 直接全局 position 匹配
- 修改后：第一轮失败 → 同 seg_id 位置不精确匹配 → 全局兜底

### 5. 消除 `_build_chain_ids` 重复实现
**文件**: `src/track/adapter.py:270-273`, `src/vehicle/ui_adapter.py:101-104`
- `TrackDataAdapter._build_chain_ids` 和 `TrackDataQuery._build_chain_ids` 改为委托 `self._td.get_chain_ids()`
- 原方法保留为薄包装（标记已废弃），避免破坏外部引用

### 6. `track_view.py` 渲染传 hint 消歧
**文件**: `src/ui/track_view.py:222-340`
- `_car_y` 增加 `hint_seg_id` 参数，返回 `(y, seg_id)` 元组
- `set_train_position` 缓存 `_last_head_seg_id`，消除冗余 `get_seg_id_at` 调用
- `set_target_marker` 传入 `_last_head_seg_id` 作为 hint

### 修改文件清单
```
modified:   src/dispatch/interlocking.py
modified:   src/track/adapter.py
modified:   src/track/data.py
modified:   src/track/route.py
modified:   src/ui/track_view.py
modified:   src/vehicle/ui_adapter.py
```

## 已验证

- **测试**: `tests/test_track.py` (25/26) + `tests/test_route.py` (36/36) = 61/62 通过
  - 1 个预存失败：`test_use_lateral_fork_switches_to_sideline` — `_fork_routes` 从未被 `from_absolute` 消费（见下文已知问题）
- **数据库路由端到端**: `compute_station_route(5→FSP)` 正确返回 16 段路径；`from_absolute` 往返保持 seg_id 不变
- **Web 服务器观察**: 两车（1车下行/2车上行）在各自正线上独立运行，无跨链占用，首站到站/离站正常

## 仍存在的问题（待下一个 Claude Code 会话修复）

### 🔴 P0: 梯度查询跨链污染导致 Dwell 卡住

**文件**: `src/track/data.py:get_gradient_at` (line 240-258)

**现象**: 当列车所在段（如 seg 55）上没有梯度记录时，当前回退逻辑（第 4 项修复）会走到全局位置匹配，从**其他链**的梯度记录中取值（如 seg 2 的 30‰）。列车在 dwell 期间 brake=0.0，非零梯度导致微滑 → `controller.is_stopped=False` → dwell 计时器不走 → 列车无限停留。

**重现**: 启动服务器（`python run_web.py`），两车切 auto 模式，观察 KYL 站（pos=2583m）dwell 后是否卡住。

**修复方向**:
```python
# get_gradient_at 的最终兜底应返回 0.0 而非跨链取值
# 方案 A: 完全删除第三步（全局兜底），seg 上无记录 → 返回 0.0
# 方案 B: 全局兜底时也过滤 seg_id 链（用 get_chain_ids）
```

### 🟡 P1: `_fork_routes` 未接入消歧逻辑

**文件**: `src/track/adapter.py:56,63-76`（写入）vs `_disambiguate_candidates`（未读取）

`set_fork_route` / `use_lateral_fork` 写入 `self._fork_routes`，但 `from_absolute` → `_disambiguate_candidates` 的 5 层优先级中没有任何一层检查这个字典。UI 按钮"走侧线"设置了 fork route 但底层消歧完全忽略。

**测试**: `tests/test_track.py:test_use_lateral_fork_switches_to_sideline` — 断言 `pos.segment_id == fork_seg.end_lateral` 失败，实际返回了 forward neighbor。

**修复方向**: 在 `_disambiguate_candidates` 中增加一个优先级（在进路匹配之后、同链匹配之前）检查 `hint_seg_id in self._fork_routes`。

**注意**: 当前所有 13 个车站都在正线上，无站台在侧线段上，所以此功能即使修好也暂时无调度场景触发。属于预留接口完善。

### 🟡 P2: 链盲的绝对坐标查询方法（4 个）

| 方法 | 文件:行 | 严重度 | 调用场景 |
|------|---------|--------|----------|
| `get_nearest_station_behind` | `data.py:287-292` | **中高** | `auto_drive._do_turnaround` 折返后找反向站 |
| `get_nearest_station_ahead` | `data.py:280-285` | 中 | `dispatch_manager._signal_route_segments` fallback |
| `get_station_at` | `data.py:266-271` | 低 | UI dashboard 显示 |
| `get_platform_side_at` | `data.py:273-278` | 低中 | `dispatch_manager._check_arrival` 开关门侧 |

这些方法仅用绝对 `position` 比较，在多链拓扑中可能返回错误链上的车站/站台。当前数据库 13 个站均匀分布、无同位置重叠，实际触发概率低，但无防护。

**修复方向**: 增加可选的 `hint_seg_id` 参数，或在调用方传入 `segment_id` 过滤。

### 🟢 P3: 遗留信号方法与 `_build_links` 里程兜底

- **信号**: `update_aspects_by_occupancy` / `get_aspect_at` / `get_nearest_signal_ahead`（`src/signal/system.py`）— 纯绝对坐标，不在 dispatch 关键路径上被调用
- **semantic_line**: `_build_links` line 181-191 的里程兜底 — 仅在图搜索失败时触发，当前数据库拓扑完整不会触发

这两个暂不修复，但建议加防御性日志。

## 快速命令参考

```bash
# 运行所有相关测试
python -m pytest tests/test_track.py tests/test_route.py -v

# 启动 Web 服务器观察运行
python run_web.py

# 数据库线路拓扑分析
python scripts/classify_track_topology.py

# 查看数据库线路结构
python -c "
from src.track.db_loader import DBLoader
td = DBLoader().load_from_db()
print(f'Segments: {len(td.segments)}')
print(f'Stations: {len(td.stations)}')
print(f'Root chains: {len([s for s in td.segments if s.abs_start < 0.01])}')

# 测试链消歧
chain = td.get_chain_ids(5)
print(f'Chain from seg 5: {len(chain)} segments')

# 测试 hint 消歧（在 44 链重叠区域）
sid_no_hint = td.get_seg_id_at(500.0)
sid_hint = td.get_seg_id_at(500.0, hint_seg_id=5)
print(f'get_seg_id_at(500): no_hint={sid_no_hint}, hint_seg5={sid_hint}')
"
```

## 设计约束速查

- **链定义**: `get_chain_ids()` 仅沿 `start_neighbor` / `end_neighbor` 遍历，**不包含** `start_lateral` / `end_lateral`。lateral 是支线/渡线，纳入会破坏主链消歧。
- **坐标系**: 每条根链的 `abs_start=0.0`，44 条链的坐标全线重叠。任何查询都必须带 `segment_id` 消歧。
- **站台关联**: 数据库 `platforms.seg_id` 是消歧关键 — 直接给出站台所属段，绕过绝对坐标歧义。
- **`from_absolute` 消歧优先级**: ①活动进路 → ②同链匹配 → ③同链间隙桥接 → ④主线兜底 → ⑤第一个候选
- **不要**: 在未传 hint_seg_id 的情况下信任任何基于绝对坐标的查询结果。
