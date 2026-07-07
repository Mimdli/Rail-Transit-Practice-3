"""Excel 线路数据加载器 — TODO: 实现从 Excel 读取线路数据"""

from src.track.data import TrackData


class TrackLoader:
    """线路数据加载器"""

    def __init__(self):
        self.track_data = TrackData()

    def load_from_excel(self, file_path: str) -> TrackData:
        """从 Excel 文件加载线路数据"""
        raise NotImplementedError

    def load_demo_data(self) -> TrackData:
        """加载演示用简化数据"""
        raise NotImplementedError
