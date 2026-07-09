"""Excel 线路数据加载器 — 从 线路数据(1).xls 读取并解析线路数据

解析的 Sheet:
  - 车站表 (Sheet 11) → Station
  - 站台表 (Sheet 12) → Platform
  - Seg表  (Sheet 3)  → Segment
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

        wb.release_resources()

        # 构建坐标系统
        self.track_data.build_coordinates()
        return self.track_data

    def load_demo_data(self) -> TrackData:
        """加载演示用简化数据（不依赖 Excel 文件，用于测试）

        线路拓扑::

            主线: seg1 ──→ seg2 ──→ seg3 ──→ seg4 ──→ seg5
                     │ E_Lat    │ E_Lat    │ S_Lat    │ E_Lat    │ E_Lat
                     ↓          ↓          ↓          ↓          ↓
                    seg6       seg7       seg8       seg9      seg10
                 (侧线1)    (侧线2)    (侧线3)    (侧线4)    (侧线5)

        主线总长约 1700m（从原来的 3658m 缩短），5 个车站，5 条道岔侧线。
        侧线覆盖 end_lateral 和 start_lateral 两种道岔类型。
        """
        td = self.track_data

        # ── 区段：主线 5 段 + 5 条道岔侧线 ─────────────────────
        td.segments = [
            # seg_id, length, start_neighbor, end_neighbor, start_lateral, end_lateral
            Segment(1, 400.0, 0, 2, end_lateral=6),          # GGZ → FSP，终点道岔→seg6
            Segment(2, 350.0, 1, 3, end_lateral=7),          # FSP → XW，终点道岔→seg7
            Segment(3, 350.0, 2, 4, start_lateral=8),        # XW → BDZ，起点道岔→seg8
            Segment(4, 350.0, 3, 5, end_lateral=9),          # BDZ → GTG，终点道岔→seg9
            Segment(5, 250.0, 4, 0, end_lateral=10),         # GTG 之后，终点道岔→seg10
            Segment(6, 200.0, 0, 0),                          # 侧线1（seg1 终点分出）
            Segment(7, 180.0, 0, 0),                          # 侧线2（seg2 终点分出）
            Segment(8, 150.0, 0, 0),                          # 侧线3（seg3 起点分出）
            Segment(9, 160.0, 0, 0),                          # 侧线4（seg4 终点分出）
            Segment(10, 120.0, 0, 0),                         # 侧线5（seg5 终点分出）
        ]

        # ── 车站：主线 5 站 ────────────────────────────────────
        td.stations = [
            Station(1, "GGZ", 0.0, [1, 2]),
            Station(2, "FSP", 400.0, [3, 4]),
            Station(3, "XW", 750.0, [5, 6]),
            Station(4, "BDZ", 1100.0, [7, 8]),
            Station(5, "GTG", 1450.0, [9, 10]),
        ]

        # ── 站台 ──────────────────────────────────────────────
        td.platforms = [
            Platform(1, 0.0, 1, "down", "GGZ"),
            Platform(2, 0.0, 1, "up", "GGZ"),
            Platform(3, 400.0, 2, "down", "FSP"),
            Platform(4, 400.0, 2, "up", "FSP"),
            Platform(5, 750.0, 3, "down", "XW"),
            Platform(6, 750.0, 3, "up", "XW"),
            Platform(7, 1100.0, 4, "down", "BDZ"),
            Platform(8, 1100.0, 4, "up", "BDZ"),
            Platform(9, 1450.0, 5, "down", "GTG"),
            Platform(10, 1450.0, 5, "up", "GTG"),
        ]

        # ── 限速（主线 + 侧线均有定义） ────────────────────────
        td.speed_limits = [
            SpeedLimit(1, 0.0, 400.0, 22.0),
            SpeedLimit(2, 0.0, 350.0, 22.0),
            SpeedLimit(3, 0.0, 100.0, 12.0),
            SpeedLimit(3, 100.0, 350.0, 22.0),
            SpeedLimit(4, 0.0, 350.0, 22.0),
            SpeedLimit(5, 0.0, 250.0, 22.0),
            # 侧线限速较低
            SpeedLimit(6, 0.0, 200.0, 10.0),
            SpeedLimit(7, 0.0, 180.0, 10.0),
            SpeedLimit(8, 0.0, 150.0, 10.0),
            SpeedLimit(9, 0.0, 160.0, 10.0),
            SpeedLimit(10, 0.0, 120.0, 10.0),
        ]

        # ── 坡度 ──────────────────────────────────────────────
        td.gradients = [
            Gradient(1, 0.0, 200.0, 0.0),
            Gradient(1, 200.0, 400.0, 5.0),
            Gradient(2, 0.0, 200.0, -3.0),
            Gradient(2, 200.0, 350.0, 0.0),
            Gradient(3, 0.0, 150.0, 0.0),
            Gradient(3, 150.0, 350.0, 8.0),
            Gradient(4, 0.0, 200.0, -5.0),
            Gradient(4, 200.0, 350.0, 0.0),
            Gradient(5, 0.0, 250.0, 3.0),
        ]

        # ── 信号机 ────────────────────────────────────────────
        td.signals = [
            Signal("S01", direction="up", seg_id=1, offset=150.0),
            Signal("S02", direction="up", seg_id=1, offset=350.0),
            Signal("S03", direction="up", seg_id=2, offset=150.0),
            Signal("S04", direction="up", seg_id=3, offset=150.0),
            Signal("S05", direction="up", seg_id=4, offset=150.0),
            Signal("S06", direction="up", seg_id=5, offset=100.0),
        ]

        td.build_coordinates()
        return td

    @staticmethod
    def create_demo_routes():
        """创建演示用预定义进路。

        注意：seg8 是 seg3 的 start_lateral（在 seg3 起点岔出），
        因此从 seg2 末端直接转入 seg8，进路为 [2, 8] 而非 [3, 8]。

        Returns:
            list[Route]: 7 条进路 ——
              0: "自动"（空列表，由系统动态算路）
              1: "主线全程" [1,2,3,4,5]  GGZ → GTG
              2: "GGZ→侧线1" [1,6]      从 seg1 终点转入侧线 seg6
              3: "FSP→侧线2" [2,7]      从 seg2 终点转入侧线 seg7
              4: "XW→侧线3"  [2,8]      从 seg2 末转入 seg3 起点侧线 seg8
              5: "BDZ→侧线4" [4,9]      从 seg4 终点转入侧线 seg9
              6: "GTG→侧线5" [5,10]     从 seg5 终点转入侧线 seg10
        """
        from src.track.route import Route
        return [
            Route(0, "自动", []),
            Route(1, "主线全程", [1, 2, 3, 4, 5]),
            Route(2, "GGZ→侧线1", [1, 6]),
            Route(3, "FSP→侧线2", [2, 7]),
            Route(4, "XW→侧线3", [2, 8]),
            Route(5, "BDZ→侧线4", [4, 9]),
            Route(6, "GTG→侧线5", [5, 10]),
        ]

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
            grad_val = _to_float(row[11])           # 坡度值 (col 11, ‰)
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
