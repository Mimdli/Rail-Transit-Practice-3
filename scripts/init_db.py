"""init_db.py — 创建 SQLite 数据库及所有表

用法:  python scripts/init_db.py [--reset]
         --reset: 删除已有数据库重建
"""

import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")

SCHEMA_SQL = """
-- ======== 线路拓扑 ========

CREATE TABLE IF NOT EXISTS segments (
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

CREATE TABLE IF NOT EXISTS stations (
    station_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    position REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS platforms (
    platform_id INTEGER PRIMARY KEY,
    position REAL NOT NULL,
    seg_id INTEGER NOT NULL REFERENCES segments(seg_id),
    direction TEXT NOT NULL CHECK(direction IN ('up', 'down')),
    station_id INTEGER REFERENCES stations(station_id)
);

CREATE TABLE IF NOT EXISTS switches (
    switch_id INTEGER PRIMARY KEY,
    seg_id INTEGER REFERENCES segments(seg_id),
    position REAL,
    switch_type TEXT
);

CREATE TABLE IF NOT EXISTS speed_limits (
    limit_id INTEGER PRIMARY KEY,
    seg_id INTEGER NOT NULL REFERENCES segments(seg_id),
    start_offset REAL NOT NULL,
    end_offset REAL NOT NULL,
    speed_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gradients (
    gradient_id INTEGER PRIMARY KEY,
    seg_id INTEGER NOT NULL REFERENCES segments(seg_id),
    start_offset REAL NOT NULL,
    end_offset REAL NOT NULL,
    gradient REAL NOT NULL,
    direction TEXT
);

-- ======== 信号系统 ========

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    seg_id INTEGER REFERENCES segments(seg_id),
    offset REAL,
    signal_type TEXT,
    direction TEXT
);

CREATE TABLE IF NOT EXISTS aspects (
    aspect_id INTEGER PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    aspect_name TEXT NOT NULL,
    code TEXT,
    meaning TEXT,
    speed_limit REAL,
    is_default INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS routes (
    route_id INTEGER PRIMARY KEY,
    name TEXT,
    start_signal TEXT REFERENCES signals(signal_id),
    end_signal TEXT REFERENCES signals(signal_id),
    direction TEXT
);

CREATE TABLE IF NOT EXISTS route_segs (
    path_id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES routes(route_id),
    seq INTEGER NOT NULL,
    seg_id INTEGER REFERENCES segments(seg_id)
);

-- ======== 车辆数据 ========

CREATE TABLE IF NOT EXISTS vehicle_types (
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

-- ======== 索引 ========

CREATE INDEX IF NOT EXISTS idx_segments_start_neighbor ON segments(start_neighbor);
CREATE INDEX IF NOT EXISTS idx_segments_end_neighbor ON segments(end_neighbor);
CREATE INDEX IF NOT EXISTS idx_platforms_seg_id ON platforms(seg_id);
CREATE INDEX IF NOT EXISTS idx_speed_limits_seg_id ON speed_limits(seg_id);
CREATE INDEX IF NOT EXISTS idx_gradients_seg_id ON gradients(seg_id);
CREATE INDEX IF NOT EXISTS idx_signals_seg_id ON signals(seg_id);
CREATE INDEX IF NOT EXISTS idx_aspects_signal_id ON aspects(signal_id);
CREATE INDEX IF NOT EXISTS idx_route_segs_route_id ON route_segs(route_id);
"""


def init_db(reset: bool = False):
    """创建/重置数据库"""
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"删除旧数据库: {DB_PATH}")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    print(f"数据库已创建: {DB_PATH}")
    print("所有表已就绪")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    init_db(reset=reset)
