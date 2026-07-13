# feature 分支贡献说明

本分支在同步 `main` 最新功能的基础上，保留并增强了线路仿真、网络与 UI 相关修复。**请勿将此分支合并回 `main`**（除非组长明确要求），仅作为独立开发与演示用途。

## 本轮新增（相对已合入 main 的仿真修复）

| 项 | 位置 | 说明 |
|----|------|------|
| 恢复拓扑推进 | `src/track/adapter.py` | main 回归后再次用拓扑 `advance_position`，避免岔口/末端卡死 |
| 前方最近岔口 | `adapter.find_nearest_lateral_fork` + `controls.py` | 「走侧线」按列车前方最近岔口设定，不再扫全线第一个 |
| 逐车黏着 | `dynamics_pipeline.py` | 编组跨区时每节车单独查黏着系数 |
| PLC 流式拆帧 | `src/network/tcp_plc.py` | TCP 接收缓冲，按 46 字节完整帧解析 |
| ATP 目标距离 | `src/network/codec.py` | `pack_atp_to_dmi` 正确写入 24bit 目标距离 |
| MODE 生效 | `src/network/manager.py` | `MODE=local` 默认不连外设；联调/Web 显式 `force_enable=True` |

## 既有仿真修复

| 问题 | 修复位置 | 说明 |
|------|----------|------|
| 道岔处卡死 | `dynamics_pipeline` + `adapter` | 拓扑推进替代绝对里程线性累加 |
| 1404m 大坡度 | `src/track/data.py` | 坡度归一化 + 区段感知查询 |
| 启动溜车 | `dynamics_pipeline` + `vehicle_controller` | 静止驻车；发车整列偏移 |
| 18103m 末端 | `adapter` | 共线岔点接续与最远可达启发式 |

## 测试

```cmd
set PYTHONPATH=.
python -m pytest tests/test_track.py tests/test_feature_network.py -v
```

## 运行

```cmd
set PYTHONPATH=.
python src/main.py
```

实验室联调：勾选界面「联调模式」，或将 `config/network_config.py` 中 `MODE` 设为 `lab_704`。
