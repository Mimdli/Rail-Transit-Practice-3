# 轨道交通模拟系统 — 数据库设计文档

> 数据库: SQLite3，文件位于 `data/railway.db`

---

## 总览

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   线路拓扑    │     │    信号系统    │     │    车辆数据    │
├─────────────┤     ├──────────────┤     ├──────────────┤
│ segments    │     │ signals      │     │ vehicle_types│
│ stations    │     │ aspects      │     │              │
│ platforms   │     │ routes       │     │              │
│ switches    │     │ route_segs   │     │              │
│ speed_limits│     │              │     │              │
│ gradients   │     │              │     │              │
└─────────────┘     └──────────────┘     └──────────────┘
```

---

## 表结构

### 1. segments — 线路区段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| seg_id | INTEGER | PK | 区段编号 |
| length | REAL | NOT NULL | 长度 (m) |
| start_ep_type | INTEGER | | 起点端点类型 |
| start_ep_id | INTEGER | | 起点端点编号 |
| end_ep_type | INTEGER | | 终点端点类型 |
| end_ep_id | INTEGER | | 终点端点编号 |
| start_neighbor | INTEGER | FK → segments | 起点正向相邻 SegID |
| start_lateral | INTEGER | FK → segments | 起点侧向相邻 SegID（道岔） |
| end_neighbor | INTEGER | FK → segments | 终点正向相邻 SegID |
| end_lateral | INTEGER | FK → segments | 终点侧向相邻 SegID（道岔） |

```sql
CREATE TABLE segments (
    seg_id INTEGER PRIMARY KEY,
    length REAL NOT NULL,
    start_ep_type INTEGER,
    start_ep_id INTEGER,
    end_ep_type INTEGER,
    end_ep_id INTEGER,
    start_neighbor INTEGER REFERENCES segments(seg_id),
    start_lateral INTEGER REFERENCES segments(seg_id),
    end_neighbor INTEGER REFERENCES segments(seg_id),
    end_lateral INTEGER REFERENCES segments(seg_id)
);
```

### 2. stations — 车站

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| station_id | INTEGER | PK | 车站编号 |
| name | TEXT | NOT NULL UNIQUE | 车站名称 |
| position | REAL | NOT NULL | 中心位置 (m) |

```sql
CREATE TABLE stations (
    station_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    position REAL NOT NULL
);
```

### 3. platforms — 站台

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| platform_id | INTEGER | PK | 站台编号 |
| position | REAL | NOT NULL | 站台中心位置 (m) |
| seg_id | INTEGER | FK → segments | 所属区段 |
| direction | TEXT | NOT NULL | `up` / `down` |
| station_id | INTEGER | FK → stations | 所属车站 |

```sql
CREATE TABLE platforms (
    platform_id INTEGER PRIMARY KEY,
    position REAL NOT NULL,
    seg_id INTEGER NOT NULL REFERENCES segments(seg_id),
    direction TEXT NOT NULL CHECK(direction IN ('up', 'down')),
    station_id INTEGER REFERENCES stations(station_id)
);
```

### 4. switches — 道岔

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| switch_id | INTEGER | PK | 道岔编号 |
| seg_id | INTEGER | FK → segments | 所在区段 |
| position | REAL | | 位置 (m) |
| switch_type | TEXT | | 道岔类型 |

```sql
CREATE TABLE switches (
    switch_id INTEGER PRIMARY KEY,
    seg_id INTEGER REFERENCES segments(seg_id),
    position REAL,
    switch_type TEXT
);
```

### 5. speed_limits — 静态限速

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| limit_id | INTEGER | PK | |
| seg_id | INTEGER | FK → segments | 所属区段 |
| start_offset | REAL | NOT NULL | 起点偏移 (m) |
| end_offset | REAL | NOT NULL | 终点偏移 (m) |
| speed_limit | REAL | NOT NULL | 限速值 (m/s) |

```sql
CREATE TABLE speed_limits (
    limit_id INTEGER PRIMARY KEY,
    seg_id INTEGER NOT NULL REFERENCES segments(seg_id),
    start_offset REAL NOT NULL,
    end_offset REAL NOT NULL,
    speed_limit REAL NOT NULL
);
```

### 6. gradients — 坡度

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| gradient_id | INTEGER | PK | |
| seg_id | INTEGER | FK → segments | 所属区段 |
| start_offset | REAL | NOT NULL | 起点偏移 (m) |
| end_offset | REAL | NOT NULL | 终点偏移 (m) |
| gradient | REAL | NOT NULL | 坡度值 (‰) |
| direction | TEXT | | 倾斜方向 |

```sql
CREATE TABLE gradients (
    gradient_id INTEGER PRIMARY KEY,
    seg_id INTEGER NOT NULL REFERENCES segments(seg_id),
    start_offset REAL NOT NULL,
    end_offset REAL NOT NULL,
    gradient REAL NOT NULL,
    direction TEXT
);
```

### 7. signals — 信号机

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| signal_id | TEXT | PK | 信号机编号（如 Z5, SCD1） |
| seg_id | INTEGER | FK → segments | 所在区段 |
| offset | REAL | | 区段内偏移 (m) |
| signal_type | TEXT | | 类型（进站/出站/通过/调车） |
| direction | TEXT | | 方向 (up/down) |

```sql
CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY,
    seg_id INTEGER REFERENCES segments(seg_id),
    offset REAL,
    signal_type TEXT,
    direction TEXT
);
```

### 8. aspects — 信号显示

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| aspect_id | INTEGER | PK | |
| signal_id | TEXT | FK → signals | 所属信号机 |
| aspect_name | TEXT | NOT NULL | 显示名称（如 "双黄"、"黄闪黄"） |
| code | TEXT | | 显示代码（如 HH, YY, YSY） |
| meaning | TEXT | | 含义描述 |
| speed_limit | REAL | | 该显示下的限速 (m/s) |
| is_default | INTEGER | DEFAULT 0 | 是否默认显示 |

```sql
CREATE TABLE aspects (
    aspect_id INTEGER PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    aspect_name TEXT NOT NULL,
    code TEXT,
    meaning TEXT,
    speed_limit REAL,
    is_default INTEGER DEFAULT 0
);
```

### 9. routes — 进路

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| route_id | INTEGER | PK | 进路编号 |
| name | TEXT | | 进路名称 |
| start_signal | TEXT | FK → signals | 始端信号机 |
| end_signal | TEXT | FK → signals | 终端信号机 |
| direction | TEXT | | 方向 |

```sql
CREATE TABLE routes (
    route_id INTEGER PRIMARY KEY,
    name TEXT,
    start_signal TEXT REFERENCES signals(signal_id),
    end_signal TEXT REFERENCES signals(signal_id),
    direction TEXT
);
```

### 10. route_segs — 进路路径区段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| path_id | INTEGER | PK | |
| route_id | INTEGER | FK → routes | 所属进路 |
| seq | INTEGER | NOT NULL | 路径顺序 |
| seg_id | INTEGER | FK → segments | 经过的区段 |

```sql
CREATE TABLE route_segs (
    path_id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES routes(route_id),
    seq INTEGER NOT NULL,
    seg_id INTEGER REFERENCES segments(seg_id)
);
```

### 11. vehicle_types — 车辆类型

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| type_id | INTEGER | PK | |
| name | TEXT | NOT NULL UNIQUE | 车型名称 |
| length | REAL | | 车辆长度 (m) |
| mass_empty | REAL | | 空载质量 (kg) |
| mass_full | REAL | | 满载质量 (kg) |
| max_speed | REAL | | 最高速度 (m/s) |
| max_traction | REAL | | 最大牵引加速度 (m/s²) |
| max_service_brake | REAL | | 常用制动减速度 (m/s²) |
| max_emergency_brake | REAL | | 紧急制动减速度 (m/s²) |

```sql
CREATE TABLE vehicle_types (
    type_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    length REAL,
    mass_empty REAL,
    mass_full REAL,
    max_speed REAL,
    max_traction REAL,
    max_service_brake REAL,
    max_emergency_brake REAL
);
```

---

## 索引

```sql
CREATE INDEX idx_segments_start_neighbor ON segments(start_neighbor);
CREATE INDEX idx_segments_end_neighbor ON segments(end_neighbor);
CREATE INDEX idx_platforms_seg_id ON platforms(seg_id);
CREATE INDEX idx_speed_limits_seg_id ON speed_limits(seg_id);
CREATE INDEX idx_gradients_seg_id ON gradients(seg_id);
CREATE INDEX idx_signals_seg_id ON signals(seg_id);
CREATE INDEX idx_aspects_signal_id ON aspects(signal_id);
CREATE INDEX idx_route_segs_route_id ON route_segs(route_id);
```

---

## Schema 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1 | 2026-07-07 | 初始设计，11 张表 |
