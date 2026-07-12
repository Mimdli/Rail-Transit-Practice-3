# feature 分支贡献说明

本分支在合并 `main` 最新功能的基础上，保留并增强了线路仿真与可视化相关修复。**请勿将此分支合并回 `main`**，仅作为独立开发与演示用途。

## 仿真修复

| 问题 | 修复位置 | 说明 |
|------|----------|------|
| 道岔处卡死（758m） | `src/vehicle/dynamics_pipeline.py` | 位置推进改用 `track.advance_position()` 拓扑推进，替代绝对里程线性累加 |
| 1404m 大坡度卡死 | `src/track/data.py` | `normalize_gradient_value()` 修正异常坡度单位；`get_gradient_at(..., seg_id)` 区段感知查询 |
| 启动即溜车 | `dynamics_pipeline.py` + `vehicle_controller.py` | 无牵引无制动且静止时驻车；发车时头车偏移整列车长度 |
| 线路末端 18103m 卡死 | `src/track/adapter.py` | 拓扑 `advance_position`、共线岔点 `_overlap_forward_at`、最远可达启发式 `_pick_forward_exit` |

## 适配器增强（`src/track/adapter.py`）

- 合并 `main` 的进路消歧（Route / 主线启发式 / 兜底三层策略）
- 保留 feature 的岔口路由 API：`set_fork_route` / `use_lateral_fork` / `clear_fork_routes`
- 坡度查询携带 `seg_id`，避免岔区重叠里程歧义

## UI 增强

- **控制面板 · 岔口进路**：「走侧线」「恢复主线」按钮，调用适配器岔口 API
- **线路可视化**：「锁定视角」跟随列车；合并 `main` 的语义化 ATS 布局

## 测试

`tests/test_track.py` 新增/保留：

- 过岔默认走主线
- 侧线岔口设定
- 坡度归一化
- DB 线路 1404m / 1400m 通行
- 静止驻车
- 线路末端可达

## 运行

```cmd
set PYTHONPATH=.
python src/main.py
```

验证环境：Python 3.9（`py39`）
