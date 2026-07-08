"""TrackEditor — 轨道数据编辑器 API

提供对 SQLite 数据库中线路数据的增删改查（CRUD）操作。
每个表（stations, platforms, segments, speed_limits, gradients, signals）
都有对应的 add/update/delete/list 方法。

编辑后调用 load_to_track_data() 重新加载到 TrackData 对象。
"""

import sqlite3
import os
from typing import Optional, List, Dict, Any

from src.track.data import TrackData
from src.track.db_loader import DB_PATH


class TrackEditor:
    """轨道数据编辑器"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()

    # ---- 连接管理 ----

    def _connect(self):
        """打开数据库连接"""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"数据库不存在: {self.db_path}")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def commit(self):
        """提交事务"""
        if self.conn:
            self.conn.commit()

    def rollback(self):
        """回滚事务"""
        if self.conn:
            self.conn.rollback()

    # ---- 通用查询 ----

    def _fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def _fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    # ---- Stations ----

    def list_stations(self) -> List[Dict[str, Any]]:
        """查询所有车站"""
        return self._fetch_all("SELECT * FROM stations ORDER BY station_id")

    def get_station(self, station_id: int) -> Optional[Dict[str, Any]]:
        """查询单个车站"""
        return self._fetch_one("SELECT * FROM stations WHERE station_id = ?", (station_id,))

    def add_station(self, station_id: int, name: str, position: float) -> int:
        """新增车站"""
        self.conn.execute(
            "INSERT INTO stations (station_id, name, position) VALUES (?, ?, ?)",
            (station_id, name, position),
        )
        return station_id

    def update_station(self, station_id: int, **kwargs) -> bool:
        """更新车站字段（name, position）"""
        return self._update("stations", station_id, kwargs)

    def delete_station(self, station_id: int) -> bool:
        """删除车站"""
        cur = self.conn.execute("DELETE FROM stations WHERE station_id = ?", (station_id,))
        return cur.rowcount > 0

    # ---- Platforms ----

    def list_platforms(self) -> List[Dict[str, Any]]:
        return self._fetch_all("SELECT * FROM platforms ORDER BY platform_id")

    def get_platform(self, platform_id: int) -> Optional[Dict[str, Any]]:
        return self._fetch_one("SELECT * FROM platforms WHERE platform_id = ?", (platform_id,))

    def add_platform(self, platform_id: int, position: float, seg_id: int,
                     direction: str, station_id: int = 0) -> int:
        self.conn.execute(
            "INSERT INTO platforms (platform_id, position, seg_id, direction, station_id) VALUES (?, ?, ?, ?, ?)",
            (platform_id, position, seg_id, direction, station_id),
        )
        return platform_id

    def update_platform(self, platform_id: int, **kwargs) -> bool:
        return self._update("platforms", platform_id, kwargs)

    def delete_platform(self, platform_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM platforms WHERE platform_id = ?", (platform_id,))
        return cur.rowcount > 0

    # ---- Segments ----

    def list_segments(self) -> List[Dict[str, Any]]:
        return self._fetch_all("SELECT * FROM segments ORDER BY seg_id")

    def get_segment(self, seg_id: int) -> Optional[Dict[str, Any]]:
        return self._fetch_one("SELECT * FROM segments WHERE seg_id = ?", (seg_id,))

    def add_segment(self, seg_id: int, length: float, start_neighbor: int = 0,
                    end_neighbor: int = 0, start_lateral: int = 0,
                    end_lateral: int = 0, **kwargs) -> int:
        # 0 和 65535 表示无邻居，转为 None 以避免外键约束失败
        def _val(v):
            return None if v in (0, 65535) else v

        self.conn.execute(
            "INSERT INTO segments (seg_id, length, start_neighbor, end_neighbor, "
            "start_lateral, end_lateral) VALUES (?, ?, ?, ?, ?, ?)",
            (seg_id, length, _val(start_neighbor), _val(end_neighbor),
             _val(start_lateral), _val(end_lateral)),
        )
        return seg_id

    def update_segment(self, seg_id: int, **kwargs) -> bool:
        return self._update("segments", seg_id, kwargs)

    def delete_segment(self, seg_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM segments WHERE seg_id = ?", (seg_id,))
        return cur.rowcount > 0

    # ---- Speed Limits ----

    def list_speed_limits(self, seg_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if seg_id:
            return self._fetch_all(
                "SELECT * FROM speed_limits WHERE seg_id = ? ORDER BY start_offset", (seg_id,))
        return self._fetch_all("SELECT * FROM speed_limits ORDER BY limit_id")

    def get_speed_limit(self, limit_id: int) -> Optional[Dict[str, Any]]:
        return self._fetch_one("SELECT * FROM speed_limits WHERE limit_id = ?", (limit_id,))

    def add_speed_limit(self, seg_id: int, start_offset: float,
                        end_offset: float, speed_limit: float) -> int:
        """新增限速区段，返回新 limit_id"""
        cur = self.conn.execute(
            "INSERT INTO speed_limits (seg_id, start_offset, end_offset, speed_limit) VALUES (?, ?, ?, ?)",
            (seg_id, start_offset, end_offset, speed_limit),
        )
        return cur.lastrowid

    def update_speed_limit(self, limit_id: int, **kwargs) -> bool:
        return self._update("speed_limits", limit_id, kwargs, pk_name="limit_id")

    def delete_speed_limit(self, limit_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM speed_limits WHERE limit_id = ?", (limit_id,))
        return cur.rowcount > 0

    # ---- Gradients ----

    def list_gradients(self, seg_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if seg_id:
            return self._fetch_all(
                "SELECT * FROM gradients WHERE seg_id = ? ORDER BY start_offset", (seg_id,))
        return self._fetch_all("SELECT * FROM gradients ORDER BY gradient_id")

    def get_gradient(self, gradient_id: int) -> Optional[Dict[str, Any]]:
        return self._fetch_one("SELECT * FROM gradients WHERE gradient_id = ?", (gradient_id,))

    def add_gradient(self, seg_id: int, start_offset: float, end_offset: float,
                     gradient: float, direction: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO gradients (seg_id, start_offset, end_offset, gradient, direction) VALUES (?, ?, ?, ?, ?)",
            (seg_id, start_offset, end_offset, gradient, direction),
        )
        return cur.lastrowid

    def update_gradient(self, gradient_id: int, **kwargs) -> bool:
        return self._update("gradients", gradient_id, kwargs, pk_name="gradient_id")

    def delete_gradient(self, gradient_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM gradients WHERE gradient_id = ?", (gradient_id,))
        return cur.rowcount > 0

    # ---- Signals ----

    def list_signals(self) -> List[Dict[str, Any]]:
        return self._fetch_all("SELECT * FROM signals ORDER BY signal_id")

    def add_signal(self, signal_id: str, seg_id: int, offset: float,
                   signal_type: str = "", direction: str = ""):
        self.conn.execute(
            "INSERT INTO signals (signal_id, seg_id, offset, signal_type, direction) VALUES (?, ?, ?, ?, ?)",
            (signal_id, seg_id, offset, signal_type, direction),
        )

    def update_signal(self, signal_id: str, **kwargs) -> bool:
        keys = []
        values = []
        for k, v in kwargs.items():
            keys.append(f"{k} = ?")
            values.append(v)
        values.append(signal_id)
        sql = f"UPDATE signals SET {', '.join(keys)} WHERE signal_id = ?"
        cur = self.conn.execute(sql, values)
        return cur.rowcount > 0

    def delete_signal(self, signal_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM signals WHERE signal_id = ?", (signal_id,))
        return cur.rowcount > 0

    # ---- 内部辅助 ----

    def _update(self, table: str, pk_value: int, fields: dict,
                pk_name: str = "station_id") -> bool:
        if not fields:
            return False
        keys = []
        values = []
        for k, v in fields.items():
            if k in (pk_name,):
                continue
            keys.append(f"{k} = ?")
            values.append(v)
        if not keys:
            return False
        values.append(pk_value)
        sql = f"UPDATE {table} SET {', '.join(keys)} WHERE {pk_name} = ?"
        cur = self.conn.execute(sql, values)
        return cur.rowcount > 0

    # ---- 重新加载到 TrackData ----

    def load_to_track_data(self) -> TrackData:
        """将当前的数据库内容重新加载为 TrackData 对象"""
        from src.track.db_loader import DBLoader
        loader = DBLoader()
        td = loader.load_from_db(self.db_path)

        # 编辑器修改后可能需要重建坐标
        from src.track.data import TrackData as TD
        td.build_coordinates()
        return td

    # ---- 批量编辑 ----

    def update_speed_limit_on_seg(self, seg_id: int, new_limit: float) -> int:
        """将某个区段的所有限速值改为新值，返回修改条数"""
        cur = self.conn.execute(
            "UPDATE speed_limits SET speed_limit = ? WHERE seg_id = ?",
            (new_limit, seg_id),
        )
        return cur.rowcount

    def clear_speed_limits_on_seg(self, seg_id: int) -> int:
        """删除某个区段的所有限速"""
        cur = self.conn.execute(
            "DELETE FROM speed_limits WHERE seg_id = ?", (seg_id,))
        return cur.rowcount

    def copy_segment(self, src_seg_id: int, new_seg_id: int) -> int:
        """复制区段（含该段上的所有限速和坡度）"""
        src = self.get_segment(src_seg_id)
        if not src:
            return 0
        self.add_segment(new_seg_id, src["length"])

        # 复制限速
        for sl in self.list_speed_limits(src_seg_id):
            self.add_speed_limit(new_seg_id, sl["start_offset"],
                                 sl["end_offset"], sl["speed_limit"])

        # 复制坡度
        for g in self.list_gradients(src_seg_id):
            self.add_gradient(new_seg_id, g["start_offset"],
                              g["end_offset"], g["gradient"], g.get("direction", ""))
        return 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
