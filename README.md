# Rail-Transit-Practice-3 — 轨道交通模拟系统

软件暑期实训3 —— 基于 Python + Qt 的轨道交通运行模拟系统。

## 项目简介

本系统面向轨道交通运行模拟，实现了一个**可演示、可交互、可扩展**的轨道交通模拟系统。

系统模拟列车在线路上的运行过程，涵盖**多体车辆动力学、线路条件、信号约束、供电状态、车门联锁、运行日志与评价、线路可视化**等核心功能，支持手动/自动驾驶演示、安全逻辑验证及运行评价。

✅ **当前状态：235 个单元测试全部通过，GUI 可正常运行。**

## 技术栈

| 技术 | 用途 |
|------|------|
| **Python 3.10+** | 主开发语言 |
| **PyQt5** | 桌面 GUI 框架，实现可视化界面与交互控制 |
| **SQLite** | 线路数据库存储（支持 CRUD 编辑） |
| **openpyxl** | 读取 Excel 格式的线路数据 |
| **pytest** | 单元测试框架 |

## 功能模块

```
┌──────────────────────────────────────────────────────────────────┐
│                       轨道交通模拟系统                              │
├────────────┬──────────┬──────────┬──────────┬────────────────────┤
│  车辆仿真   │  轨道线路  │  信号系统  │  供电系统  │   可视化与日志      │
├────────────┼──────────┼──────────┼──────────┼────────────────────┤
│ · 多体动力学 │ · 站点数据 │ · 红/黄/ │ · 正常    │ · 状态仪表盘        │
│ · 逐车力计算 │ · 道岔分支 │   绿灯    │ · 低压    │ · 驾驶控制面板       │
│ · 车钩力    │ · 限速区段 │ · 闭塞占用 │ · 断电    │ · 线路全景图         │
│ · 牵引/制动 │ · 坡度区段 │ · 黄灯限速 │ · 故障恢复 │ · 速度/车钩力曲线     │
│ · 自动驾驶  │ · 信号机  │ · 协议灯码 │          │ · 力分量明细表        │
│ · 车门联锁  │ · 站台侧  │           │          │ · 运行日志与评价       │
│ · 能耗估算  │ · 数据库  │           │          │ · 线路数据编辑器       │
└────────────┴──────────┴──────────┴──────────┴────────────────────┘
```

### 1. 车辆仿真模块 (`src/vehicle/`)

#### 多体动力学控制器（新版，当前主力）

`VehicleController` 是列车顶层控制器，管理编组状态、控制指令和仿真步进。每个仿真步内：

1. 控制指令（`throttle` / `brake_level`）经过一阶低通滤波，模拟牵引建磁和制动响应迟滞
2. 外部步长拆分为多个物理微步，保证车钩力和高速积分稳定性
3. 逐车查询所在位置的限速、坡度、曲线、隧道和黏着条件
4. 逐车计算 Davis 阻力、坡度阻力、隧道阻力、曲线阻力、牵引力、电制动力和摩擦制动力
5. 根据相邻车辆位置差、速度差和车钩参数计算车钩力
6. 聚合每节车合力，按等效质量更新单车速度、位置和加速度
7. 限速裁剪后生成 `ForceReport`，记录各力分量和限幅情况

| 模块 | 文件 | 说明 |
|------|------|------|
| 顶层控制器 | `vehicle_controller.py` | 编组管理、指令滤波、步进调度 |
| 动力学流水线 | `dynamics_pipeline.py` | 逐车力计算与状态积分 |
| 力计算 | `forces.py` | 牵引/制动/阻力/坡度等力分量 |
| 车钩力 | `coupler.py` | 相邻车辆间车钩力建模 |
| 自动驾驶 | `auto_drive.py` | 目标停车、巡航、紧急制动三段控制 |
| 环境接口 | `environment.py` | 天气、线路查询接口（`MockEnvironment`） |
| 枚举定义 | `enums.py` | 运行模式、载荷等级、车门侧、控制级位 |
| 力报告 | `force_report.py` | 动力学计算结果的数据结构 |
| UI 适配层 | `ui_adapter.py` | 将 `TrackData` 适配为 `ITrackQuery`，桥接新旧模块 |

#### 旧单质点模型（已废弃，保留兼容）

`VehicleModel` / `Controller` 为早期单质点实现，不再维护。Qt 前端已完全迁移至 `VehicleController`。

### 2. 轨道线路模块 (`src/track/`)

| 模块 | 文件 | 说明 |
|------|------|------|
| 线路数据结构 | `data.py` | `TrackData`、`Station`、`Platform`、`Segment`、`SpeedLimit`、`Gradient`、`Signal` |
| Excel 加载器 | `loader.py` | 从 Excel 读取线路数据，构建 `TrackData` |
| 数据库加载器 | `db_loader.py` | 从 SQLite 数据库加载线路数据 |
| 线路编辑器 | `editor.py` | 对 SQLite 中线路数据进行 CRUD 操作 |
| 车辆适配器 | `adapter.py` | `TrackDataAdapter`：将 `TrackData` 包装为 `ITrackQuery` 接口 |

**线路数据源切换**：界面支持「演示数据」（Excel）和「数据库线路」（SQLite）两种模式。

### 3. 信号系统模块 (`src/signal/`)

- **绿灯** — 允许正常运行
- **黄灯** — 允许运行，需降低速度（限速 10 m/s）
- **红灯** — 禁止越过，防护状态

信号计算逻辑：
1. 按运行方向对信号机排序
2. 相邻信号机之间视为简化闭塞分区
3. 根据前车位置判断闭塞占用，生成红/黄/绿状态
4. 支持协议灯色码（0x01~0x30）归约为红/黄/绿三态
5. 有效限速 = `min(线路限速, 信号限速)`

### 4. 供电状态模块 (`src/power/`)

模拟供电条件对牵引能力的影响：

| 状态 | 牵引限制 |
|------|----------|
| 供电正常 | 100% 牵引能力 |
| 电压低 | 限制为 50% 牵引力 |
| 断电 | 禁止牵引 |
| 故障恢复 | 3 秒后恢复供电 |

### 5. 车门联锁模块 (`src/door/`)

`DoorInterlock` 保证停车、开门、发车符合安全规则：
- 车速 > 0.5 m/s 时禁止开门
- 车门未关时禁止发车
- 根据站台侧自动判断开左门/右门
- 兼容新旧车辆控制器

### 6. 网络通信模块 (`src/network/`)

提供与外部真实硬件的网络通信能力（独立线程，不阻塞 UI）：

| 子系统 | 协议 | 周期 | 用途 |
|--------|------|------|------|
| 车辆 UDP | UDP | 20ms | 收发车辆加速度、速度、距离 |
| 信号网关 | UDP | 250ms/100ms | 信号状态同步 |
| 司机台 PLC | TCP | 100ms | 司机操作输入、ATP 安全输出 |

> 当前为框架预留，默认关闭。通过 `_network_mode` 开关切换本地/网络模式。

### 7. 可视化界面 (`src/ui/`)

| 组件 | 文件 | 说明 |
|------|------|------|
| 主窗口 | `main_window.py` | 应用主界面，Tab 切换、定时更新循环 |
| 状态仪表盘 | `dashboard.py` | 速度/限速/位置/坡度/信号/供电/车门/编组 |
| 控制面板 | `controls.py` | 牵引/制动/惰行/紧急制动/车门/载荷/模式切换 |
| 线路全景图 | `track_view.py` | QGraphicsView 绘制轨道、道岔、信号机、列车位置 |
| 力学分析面板 | `charts.py` | 速度-时间曲线、车钩力曲线、逐车力分量明细表 |

### 8. 日志与评价模块 (`src/logger/`)

| 模块 | 文件 | 说明 |
|------|------|------|
| 事件记录器 | `recorder.py` | 运行事件记录，CSV 导出 |
| 运行评价器 | `evaluator.py` | 停车误差、最大速度、超速次数、安全事件统计 |

## 公共数据结构 (`src/common/`)

| 模块 | 说明 |
|------|------|
| `car_config.py` | 单车参数（质量、车长、牵引/制动特性）与车钩参数 |
| `car_state.py` | 单车运行时状态（位置、速度、加速度） |
| `consist.py` | 列车编组定义（预设：4M2T 六节编组） |
| `track_position.py` | 线路位置抽象（`TrackPosition`）与查询接口（`ITrackQuery`） |

## 项目结构

```
Rail-Transit-Practice-3/
├── resource/                   # 资源文件
│   ├── 线路数据(1).xls         # 原始线路 Excel 数据
│   └── 初步目标需求分析.md      # 需求分析文档
├── data/                       # 运行时数据
│   └── railway.db              # SQLite 线路数据库
├── src/                        # 源代码
│   ├── main.py                 # 程序入口
│   ├── common/                 # 公共数据结构
│   │   ├── car_config.py       # 单车参数
│   │   ├── car_state.py        # 单车状态
│   │   ├── consist.py          # 列车编组
│   │   └── track_position.py   # 位置抽象与查询接口
│   ├── vehicle/                # 车辆仿真模块
│   │   ├── vehicle_controller.py  # 多体车辆顶层控制器
│   │   ├── dynamics_pipeline.py   # 逐车动力学计算流水线
│   │   ├── forces.py           # 牵引/制动/阻力计算
│   │   ├── coupler.py          # 车钩力计算
│   │   ├── auto_drive.py       # 自动驾驶控制器
│   │   ├── environment.py      # 环境查询接口
│   │   ├── enums.py            # 枚举定义
│   │   ├── force_report.py     # 动力学计算报告
│   │   ├── ui_adapter.py       # UI 适配层
│   │   ├── model.py            # (废弃) 旧单质点模型
│   │   └── controller.py       # (废弃) 旧控制器
│   ├── track/                  # 轨道线路模块
│   │   ├── data.py             # 线路数据结构
│   │   ├── loader.py           # Excel 数据加载
│   │   ├── db_loader.py        # SQLite 数据库加载
│   │   ├── editor.py           # 线路数据编辑器
│   │   └── adapter.py          # TrackData → ITrackQuery 适配
│   ├── signal/                 # 信号系统模块
│   │   └── system.py           # 信号状态逻辑
│   ├── power/                  # 供电状态模块
│   │   └── supply.py           # 供电状态管理
│   ├── door/                   # 车门联锁模块
│   │   └── interlock.py        # 车门与站台联锁
│   ├── network/                # 网络通信模块（预留）
│   │   ├── manager.py          # 通信管理器
│   │   ├── udp_vehicle.py      # 车辆 UDP 通信
│   │   ├── signal_gateway.py   # 信号网关
│   │   ├── tcp_plc.py          # 司机台 PLC
│   │   ├── codec.py            # 编解码
│   │   └── constants.py        # 网络常量
│   ├── logger/                 # 日志与评价模块
│   │   ├── recorder.py         # 事件记录
│   │   └── evaluator.py        # 运行评价
│   └── ui/                     # 可视化界面
│       ├── main_window.py      # 主窗口
│       ├── dashboard.py        # 状态仪表盘
│       ├── controls.py         # 控制面板
│       ├── track_view.py       # 线路全景图
│       └── charts.py           # 速度曲线与力分量表
├── tests/                      # 单元测试（235 个，全部通过）
│   ├── test_vehicle.py
│   ├── test_controller.py
│   ├── test_dynamics_pipeline.py
│   ├── test_forces.py
│   ├── test_auto_drive.py
│   ├── test_coupler.py
│   ├── test_consist.py
│   ├── test_car_config.py
│   ├── test_car_state.py
│   ├── test_environment.py
│   ├── test_force_report.py
│   ├── test_track_position.py
│   ├── test_track.py
│   ├── test_signal.py
│   ├── test_recorder.py
│   ├── test_editor.py
│   ├── test_vehicle_ui_adapter.py
│   └── __init__.py
├── web/                        # Web 原型（线路可视化原型）
├── output/                     # 运行输出
├── .gitignore
├── README.md
└── requirements.txt
```

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行程序

```bash
python -m src.main
```

### 运行测试

```bash
pytest tests/ -v
```

## 需求进度

| 优先级 | 内容 | 状态 |
|--------|------|------|
| **Must** | 车辆运行、线路站点、限速、坡度、手动控制、状态显示、日志记录 | ✅ 完成 |
| **Must** | 多体车辆动力学（逐车计算、车钩力） | ✅ 完成 |
| **Should** | 自动停站、信号约束、供电异常、车门联锁 | ✅ 完成 |
| **Should** | 线路全景可视化、力学分析面板 | ✅ 完成 |
| **Should** | SQLite 数据库线路加载与编辑 | ✅ 完成 |
| **Could** | 天气影响、载荷模式、黏着控制 | ✅ 完成 |
| **Could** | 网络通信接口（UDP/信号网关/PLC） | 🔧 框架预留 |
| **Could** | Web 线路可视化原型 | 🚧 开发中 |
| **Won't** | 完整 CBTC、完整联锁表、真实供电网络 | — |

## 后续扩展方向

- **网络模式接入**：将 `NetworkManager` 与实际外部硬件联调
- **Web 可视化完善**：`web/` 目录下的 Web 原型继续开发
- **CBTC 移动闭塞**：从简化固定闭塞升级为移动闭塞
- **完整联锁表**：支持真实联锁进路逻辑
