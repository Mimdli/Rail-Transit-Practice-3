"""Excel 线路数据加载器"""

from typing import Optional
from src.track.data import TrackData, Station, Platform, Segment, SpeedLimit, Gradient, Signal


class TrackLoader:
    """线路数据加载器 — 从 Excel 文件读取线路数据"""

    def __init__(self):
        self.track_data = TrackData()

    def load_from_excel(self, file_path: str) -> TrackData:
        """从 Excel 文件加载线路数据"""
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise ImportError("请安装 openpyxl: pip install openpyxl")

        wb = load_workbook(file_path, data_only=True)

        self._load_stations(wb)
        self._load_platforms(wb)
        self._load_segments(wb)
        self._load_speed_limits(wb)
        self._load_gradients(wb)
        self._load_signals(wb)

        wb.close()
        return self.track_data

    def load_demo_data(self) -> TrackData:
        """加载演示用简化数据（不依赖 Excel 文件）"""
        td = self.track_data

        # 车站
        td.stations = [
            Station("车站A", 0.0),
            Station("车站B", 500.0),
            Station("车站C", 1000.0),
            Station("车站D", 1500.0),
            Station("车站E", 2000.0),
        ]

        # 站台
        td.platforms = [
            Platform("车站A", 0.0, "right"),
            Platform("车站B", 500.0, "right"),
            Platform("车站C", 1000.0, "right"),
            Platform("车站D", 1500.0, "right"),
            Platform("车站E", 2000.0, "right"),
        ]

        # 区段
        td.segments = [
            Segment("S01", 0.0, 100.0),
            Segment("S02", 100.0, 250.0),
            Segment("S03", 250.0, 500.0),
            Segment("S04", 500.0, 700.0),
            Segment("S05", 700.0, 1000.0),
            Segment("S06", 1000.0, 1250.0),
            Segment("S07", 1250.0, 1500.0),
            Segment("S08", 1500.0, 1750.0),
            Segment("S09", 1750.0, 2000.0),
        ]

        # 限速区段
        td.speed_limits = [
            SpeedLimit(0.0, 400.0, 22.0),
            SpeedLimit(400.0, 600.0, 15.0),
            SpeedLimit(600.0, 900.0, 22.0),
            SpeedLimit(900.0, 1100.0, 12.0),
            SpeedLimit(1100.0, 1400.0, 22.0),
            SpeedLimit(1400.0, 1600.0, 15.0),
            SpeedLimit(1600.0, 2000.0, 22.0),
        ]

        # 坡度区段
        td.gradients = [
            Gradient(0.0, 200.0, 0.0),
            Gradient(200.0, 350.0, 5.0),
            Gradient(350.0, 500.0, -3.0),
            Gradient(500.0, 700.0, 0.0),
            Gradient(700.0, 850.0, 8.0),
            Gradient(850.0, 1000.0, 0.0),
            Gradient(1000.0, 1200.0, -5.0),
            Gradient(1200.0, 1500.0, 0.0),
            Gradient(1500.0, 1700.0, 3.0),
            Gradient(1700.0, 2000.0, 0.0),
        ]

        # 信号机
        td.signals = [
            Signal("S01", 100.0, "up"),
            Signal("S02", 250.0, "up"),
            Signal("S03", 500.0, "up"),
            Signal("S04", 700.0, "up"),
            Signal("S05", 1000.0, "up"),
            Signal("S06", 1250.0, "up"),
            Signal("S07", 1500.0, "up"),
            Signal("S08", 1750.0, "up"),
            Signal("S09", 2000.0, "up"),
        ]

        return td

    def _load_stations(self, wb):
        """加载车站表"""
        # TODO: 从 Excel Sheet 读取
        pass

    def _load_platforms(self, wb):
        """加载站台表"""
        pass

    def _load_segments(self, wb):
        """加载区段表"""
        pass

    def _load_speed_limits(self, wb):
        """加载限速表"""
        pass

    def _load_gradients(self, wb):
        """加载坡度表"""
        pass

    def _load_signals(self, wb):
        """加载信号机表"""
        pass
