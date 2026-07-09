"""Excel 线路数据加载器 — 从 线路数据(1).xls 读取并解析线路数据

解析的 Sheet:
  - 车站表 (Sheet 11) → Station
  - 站台表 (Sheet 12) → Platform
  - Seg表  (Sheet 3)  → Segment
  - 信号机表 (Sheet 9) → Signal
  - 静态限速表 (Sheet 15) → SpeedLimit
  - 坡度表 (Sheet 14) → Gradient

数据格式说明:
  - 每个 Sheet 前 3 行为元数据/表头，第 4 行起为数据
  - 距离单位: cm（内部转换为 m）
  - 限速单位: cm/s（内部转换为 m/s）
  - 65535 代表空值
"""

import re
from typing import Optional

from src.track.data import (
    TrackData, Station, Platform, Segment,
    SpeedLimit, Gradient, Signal
)


# 公里标解析: "K0+313.000" → 313.0 m,  "K12+500.000" → 12500.0 m
_KM_PATTERN = re.compile(r"K(\d+)\+([\d.]+)")


def _parse_km(km_str) -> float:
    """解析公里标字符串为米"""
    if not isinstance(km_str, str):
        return 0.0
    m = _KM_PATTERN.match(km_str.strip())
    if m:
        km = float(m.group(1))
        m_val = float(m.group(2))
        return km * 1000 + m_val
    return 0.0


def _to_int(val, default=0) -> int:
    """安全转换为 int，处理 None/空字符串/65535"""
    if val is None:
        return default
    try:
        v = int(float(str(val)))
        return v if v != 65535 else default
    except (ValueError, TypeError):
        return default


def _to_float(val, default=0.0) -> float:
    """安全转换为 float"""
    if val is None:
        return default
    try:
        v = float(str(val))
        return v if v != 65535.0 else default
    except (ValueError, TypeError):
        return default


def _cm_to_m(cm_val) -> float:
    """厘米转米"""
    return _to_float(cm_val) / 100.0


def _cm_s_to_m_s(cm_s_val) -> float:
    """cm/s 转 m/s"""
    return _to_float(cm_s_val) / 100.0


def _parse_direction(hex_val) -> str:
    """解析方向: 0x55=down, 0xaa=up"""
    if isinstance(hex_val, str):
        hex_val = hex_val.strip().lower()
        if hex_val in ("0x55", "0xaa"):
            return "down" if hex_val == "0x55" else "up"
    try:
        v = int(float(str(hex_val)))
        if v == 0x55:
            return "down"
        elif v == 0xaa:
            return "up"
    except (ValueError, TypeError):
        pass
    return "down"


def _get_row(sheet, row_idx: int) -> list:
    """获取 sheet 中某行的所有实际数据值"""
    return [sheet.cell_value(row_idx, c) for c in range(sheet.ncols)]


class TrackLoader:
    """线路数据加载器 — 从 Excel 文件读取并解析线路数据"""

    def __init__(self):
        self.track_data = TrackData()

    def load_from_excel(self, file_path: str) -> TrackData:
        """从 Excel 文件加载线路数据"""
        try:
            import xlrd
        except ImportError:
            raise ImportError("请安装 xlrd: pip install xlrd")

        wb = xlrd.open_workbook(file_path)

        self._load_segments(wb)
        self._load_stations(wb)
        self._load_platforms(wb)
        self._load_speed_limits(wb)
        self._load_gradients(wb)
        self._load_signals(wb)

        wb.release_resources()

        # 构建坐标系统
        self.track_data.build_coordinates()
        return self.track_data

    def load_demo_data(self) -> TrackData:
        """加载演示用简化数据（不依赖 Excel 文件，用于测试）"""
        td = self.track_data

        # 主线 5 段 + 2 条道岔侧线（演示分叉可视化）
        td.segments = [
            Segment(1, 758.0, 0, 2, end_lateral=6),
            Segment(2, 850.0, 1, 3),
            Segment(3, 750.0, 2, 4, end_lateral=7),
            Segment(4, 700.0, 3, 5),
            Segment(5, 600.0, 4, 0),
            Segment(6, 420.0, 0, 0),
            Segment(7, 380.0, 0, 0),
        ]

        td.stations = [
            Station(1, "GGZ", 0.0, [1, 2]),
            Station(2, "FSP", 758.0, [3, 4]),
            Station(3, "XW", 1608.0, [5, 6]),
            Station(4, "BDZ", 2358.0, [7, 8]),
            Station(5, "GTG", 3058.0, [9, 10]),
        ]

        td.platforms = [
            Platform(1, 0.0, 1, "down", "GGZ"),
            Platform(2, 0.0, 1, "up", "GGZ"),
            Platform(3, 758.0, 2, "down", "FSP"),
            Platform(4, 758.0, 2, "up", "FSP"),
            Platform(5, 1608.0, 3, "down", "XW"),
            Platform(6, 1608.0, 3, "up", "XW"),
            Platform(7, 2358.0, 4, "down", "BDZ"),
            Platform(8, 2358.0, 4, "up", "BDZ"),
            Platform(9, 3058.0, 5, "down", "GTG"),
            Platform(10, 3058.0, 5, "up", "GTG"),
        ]

        td.speed_limits = [
            SpeedLimit(1, 0.0, 400.0, 22.0),
            SpeedLimit(1, 400.0, 758.0, 15.0),
            SpeedLimit(2, 0.0, 300.0, 15.0),
            SpeedLimit(2, 300.0, 850.0, 22.0),
            SpeedLimit(3, 0.0, 200.0, 12.0),
            SpeedLimit(3, 200.0, 750.0, 22.0),
            SpeedLimit(4, 0.0, 350.0, 15.0),
            SpeedLimit(4, 350.0, 700.0, 22.0),
            SpeedLimit(5, 0.0, 600.0, 22.0),
            SpeedLimit(6, 0.0, 420.0, 15.0),
            SpeedLimit(7, 0.0, 380.0, 15.0),
        ]

        td.gradients = [
            Gradient(1, 0.0, 200.0, 0.0),
            Gradient(1, 200.0, 350.0, 5.0),
            Gradient(1, 350.0, 500.0, -3.0),
            Gradient(2, 0.0, 200.0, 0.0),
            Gradient(2, 200.0, 350.0, 8.0),
            Gradient(2, 350.0, 500.0, 0.0),
            Gradient(3, 0.0, 200.0, -5.0),
            Gradient(3, 200.0, 500.0, 0.0),
            Gradient(4, 0.0, 200.0, 3.0),
            Gradient(4, 200.0, 500.0, 0.0),
        ]

        # 演示信号机用于本地闭塞和红黄绿三态展示。
        td.signals = [
            Signal("S01", direction="up", seg_id=1, offset=200.0),
            Signal("S02", direction="up", seg_id=1, offset=600.0),
            Signal("S03", direction="up", seg_id=2, offset=300.0),
            Signal("S04", direction="up", seg_id=3, offset=250.0),
            Signal("S05", direction="up", seg_id=4, offset=300.0),
            Signal("S06", direction="up", seg_id=5, offset=300.0),
        ]

        td.build_coordinates()
        return td

    # ---- 内部加载方法 ----
    # 每个 sheet 的结构: 前 3 行元数据, 第 3 行(索引2)为表头, 从第 4 行(索引3)起为数据

    def _load_segments(self, wb):
        """加载 Seg表 (Sheet 3)"""
        try:
            sheet = wb.sheet_by_name("Seg表")
        except xlrd.biffh.XLRDError:
            return

        td = self.track_data
        for r in range(3, sheet.nrows):
            row = _get_row(sheet, r)
            seg_id = _to_int(row[0])
            if seg_id == 0:
                continue
            length_cm = _to_float(row[1])
            start_neighbor = _to_int(row[6])    # 起点正向相邻SegID (col 6)
            start_lateral = _to_int(row[7])     # 起点侧向相邻SegID (col 7, 道岔)
            end_neighbor = _to_int(row[8])       # 终点正向相邻SegID (col 8)
            end_lateral = _to_int(row[9])        # 终点侧向相邻SegID (col 9, 道岔)
            td.segments.append(Segment(
                seg_id=seg_id,
                length=length_cm / 100.0,       # cm → m
                start_neighbor=start_neighbor,
                end_neighbor=end_neighbor,
                start_lateral=start_lateral,
                end_lateral=end_lateral,
            ))

    def _load_stations(self, wb):
        """加载 车站表 (Sheet 11)"""
        try:
            sheet = wb.sheet_by_name("车站表")
        except xlrd.biffh.XLRDError:
            return

        td = self.track_data
        for r in range(3, sheet.nrows):
            row = _get_row(sheet, r)
            sid = _to_int(row[0])
            if sid == 0:
                continue
            name = str(row[1]).strip()
            if not name:
                continue
            # 收集站台编号
            platform_ids = []
            for c in range(3, min(13, sheet.ncols)):
                pid = _to_int(row[c])
                if pid > 0:
                    platform_ids.append(pid)
            td.stations.append(Station(
                station_id=sid,
                name=name,
                position=0.0,  # 后面由站台表补充
                platform_ids=platform_ids,
            ))

    def _load_platforms(self, wb):
        """加载 站台表 (Sheet 12)"""
        try:
            sheet = wb.sheet_by_name("站台表")
        except xlrd.biffh.XLRDError:
            return

        td = self.track_data
        # 建立 station_id → Station 映射
        station_by_platform = {}
        for s in td.stations:
            for pid in s.platform_ids:
                station_by_platform[pid] = s

        for r in range(3, sheet.nrows):
            row = _get_row(sheet, r)
            pid = _to_int(row[0])
            if pid == 0:
                continue

            # 解析公里标 (col 1)
            pos = _parse_km(row[1])
            # 如果公里标无效，尝试用偏移量
            if pos == 0.0:
                pos_cm = _to_float(row[2])  # seg_id might be here
                pos = pos_cm / 100.0

            seg_id = _to_int(row[2])          # 关联seg编号 (col 2)
            direction = _parse_direction(row[3])  # 方向 (col 3)

            # 关联车站名称
            station_name = ""
            if pid in station_by_platform:
                station_name = station_by_platform[pid].name
                # 更新车站位置
                station = station_by_platform[pid]
                if station.position == 0.0 or pos < station.position:
                    station.position = pos

            td.platforms.append(Platform(
                platform_id=pid,
                position=pos,
                seg_id=seg_id,
                direction=direction,
                station_name=station_name,
            ))

    def _load_speed_limits(self, wb):
        """加载 静态限速表 (Sheet 15)"""
        try:
            sheet = wb.sheet_by_name("静态限速表")
        except xlrd.biffh.XLRDError:
            return

        td = self.track_data
        for r in range(3, sheet.nrows):
            row = _get_row(sheet, r)
            idx = _to_int(row[0])
            if idx == 0:
                continue

            seg_id = _to_int(row[1])               # 限速区段所处seg编号 (col 1)
            start_offset = _cm_to_m(row[2])        # 起点偏移 (cm → m) (col 2)
            end_offset = _cm_to_m(row[3])          # 终点偏移 (cm → m) (col 3)
            speed_cm_s = _to_float(row[5])         # 限速值 (col 5, cm/s)
            speed_ms = speed_cm_s / 100.0          # cm/s → m/s

            td.speed_limits.append(SpeedLimit(
                seg_id=seg_id,
                start_offset=start_offset,
                end_offset=end_offset,
                speed_limit=speed_ms,
            ))

    def _load_gradients(self, wb):
        """加载 坡度表 (Sheet 14)"""
        try:
            sheet = wb.sheet_by_name("坡度表")
        except xlrd.biffh.XLRDError:
            return

        td = self.track_data
        for r in range(3, sheet.nrows):
            row = _get_row(sheet, r)
            idx = _to_int(row[0])
            if idx == 0:
                continue

            start_seg = _to_int(row[1])             # 坡度起点所处seg编号 (col 1)
            start_offset = _cm_to_m(row[2])         # 起点偏移 (cm → m) (col 2)
            end_seg = _to_int(row[3])               # 坡度终点所处seg编号 (col 3)
            end_offset = _cm_to_m(row[4])           # 终点偏移 (cm → m) (col 4)
            grad_val = _to_float(row[11]) / 10.0    # 坡度值 (0.1‰ → ‰)
            direction = _parse_direction(row[12])   # 倾斜方向 (col 12)

            # 对于起终点在不同 seg 的坡度，拆分为两段
            td.gradients.append(Gradient(
                seg_id=start_seg,
                start_offset=start_offset,
                end_offset=_cm_to_m(row[4]),
                gradient=grad_val,
                direction=direction,
            ))
            if end_seg != start_seg and end_seg > 0:
                td.gradients.append(Gradient(
                    seg_id=end_seg,
                    start_offset=0.0,
                    end_offset=end_offset,
                    gradient=grad_val,
                    direction=direction,
                ))

    def _load_signals(self, wb):
        """加载 信号机表 (Sheet 9)"""
        try:
            sheet = wb.sheet_by_name("信号机表")
        except xlrd.biffh.XLRDError:
            return

        td = self.track_data
        for r in range(4, sheet.nrows):
            row = _get_row(sheet, r)
            signal_id = str(row[1]).strip()
            if not signal_id:
                continue

            seg_id = _to_int(row[4])           # 所处Seg编号 (col 4)
            if seg_id == 0:
                continue

            # Excel 中偏移量单位为 cm，TrackData 内部统一使用 m。
            offset = _cm_to_m(row[5])          # 所处Seg偏移量 (col 5)
            direction = _parse_direction(row[6])  # 防护方向 (col 6)

            td.signals.append(Signal(
                signal_id=signal_id,
                direction=direction,
                seg_id=seg_id,
                offset=offset,
            ))
