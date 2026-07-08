"""DBLoader — 从 SQLite 数据库加载线路数据到 Python 对象"""

import sqlite3
import os
from typing import Optional

from src.track.data import (
    TrackData, Station, Platform, Segment,
    SpeedLimit, Gradient, Signal
)


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "railway.db")


class DBLoader:
    """从 SQLite 数据库加载线路数据"""

    def __init__(self):
        self.track_data = TrackData()

    def load_from_db(self, db_path: Optional[str] = None) -> TrackData:
        """从 SQLite 加载所有线路数据"""
        path = db_path or DB_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(f"数据库文件不存在: {path}")

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        self._load_segments(cur)
        self._load_stations(cur)
        self._load_platforms(cur)
        self._load_speed_limits(cur)
        self._load_gradients(cur)
        self._load_signals(cur)

        conn.close()

        self.track_data.build_coordinates()
        return self.track_data

    def _load_segments(self, cur):
        cur.execute("SELECT * FROM segments ORDER BY seg_id")
        for row in cur.fetchall():
            self.track_data.segments.append(Segment(
                seg_id=row["seg_id"],
                length=row["length"],
                start_neighbor=row["start_neighbor"] or 0,
                end_neighbor=row["end_neighbor"] or 0,
                start_lateral=row["start_lateral"] or 0,
                end_lateral=row["end_lateral"] or 0,
            ))

    def _load_stations(self, cur):
        cur.execute("SELECT * FROM stations ORDER BY station_id")
        for row in cur.fetchall():
            self.track_data.stations.append(Station(
                station_id=row["station_id"],
                name=row["name"],
                position=row["position"],
            ))

    def _load_platforms(self, cur):
        cur.execute("SELECT * FROM platforms ORDER BY platform_id")
        for row in cur.fetchall():
            self.track_data.platforms.append(Platform(
                platform_id=row["platform_id"],
                position=row["position"],
                seg_id=row["seg_id"],
                direction=row["direction"],
                station_name="",
            ))

    def _load_speed_limits(self, cur):
        cur.execute("SELECT * FROM speed_limits ORDER BY limit_id")
        for row in cur.fetchall():
            self.track_data.speed_limits.append(SpeedLimit(
                seg_id=row["seg_id"],
                start_offset=row["start_offset"],
                end_offset=row["end_offset"],
                speed_limit=row["speed_limit"],
            ))

    def _load_gradients(self, cur):
        cur.execute("SELECT * FROM gradients ORDER BY gradient_id")
        for row in cur.fetchall():
            self.track_data.gradients.append(Gradient(
                seg_id=row["seg_id"],
                start_offset=row["start_offset"],
                end_offset=row["end_offset"],
                gradient=row["gradient"],
                direction=row["direction"] or "",
            ))

    def _load_signals(self, cur):
        cur.execute("SELECT signal_id, seg_id, offset, direction FROM signals ORDER BY signal_id")
        for row in cur.fetchall():
            self.track_data.signals.append(Signal(
                signal_id=row["signal_id"],
                direction=row["direction"] or "",
                seg_id=row["seg_id"],
                offset=row["offset"] or 0.0,
            ))
