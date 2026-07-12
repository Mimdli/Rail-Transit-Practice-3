"""DBLoader — 从 SQLite 数据库加载线路数据到 Python 对象"""

import sqlite3
import os
import logging
from statistics import mean
from typing import Optional

from src.track.data import (
    TrackData, Station, Platform, Segment,
    SpeedLimit, Gradient, Signal
)


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "railway.db")
logger = logging.getLogger(__name__)


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
        self._finalize_station_platforms()
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
                station_id=row["station_id"] or 0,
            ))

    def _finalize_station_platforms(self):
        """关联站台与车站，并在内存中修复数据库缺失的车站里程。"""
        platforms_by_station: dict[int, list[Platform]] = {}
        for platform in self.track_data.platforms:
            platforms_by_station.setdefault(platform.station_id, []).append(platform)

        repaired = []
        for station in self.track_data.stations:
            platforms = platforms_by_station.get(station.station_id, [])
            station.platform_ids = [platform.platform_id for platform in platforms]
            for platform in platforms:
                platform.station_name = station.name

            if station.position > 0 or not platforms:
                continue
            candidates = []
            for platform in platforms:
                segment = self.track_data._seg_map.get(platform.seg_id)
                if segment is not None:
                    candidates.append(segment.abs_start + segment.length / 2.0)
            if not candidates:
                continue
            station.position = mean(candidates)
            for platform in platforms:
                if platform.position <= 0:
                    platform.position = station.position
            repaired.append(station.name)

        if repaired:
            message = (
                "数据库缺失车站里程，已按所属站台区段中心在内存中补齐: "
                + ", ".join(repaired)
            )
            self.track_data.data_warnings.append(message)
            logger.info(message)

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
