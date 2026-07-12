"""import_to_db.py — 将线路数据(1).xls 导入到 SQLite 数据库

用法:  python scripts/import_to_db.py
"""

import sqlite3
import os
import sys
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")
XLS_PATH = os.path.join(os.path.dirname(__file__), "..", "resource", "线路数据(1).xls")

# 工具函数
_KM_PATTERN = re.compile(r"K(\d+)\+([\d.]+)")


def _parse_km(km_str) -> float:
    if not isinstance(km_str, str):
        return 0.0
    m = _KM_PATTERN.match(km_str.strip())
    if m:
        return float(m.group(1)) * 1000 + float(m.group(2))
    return 0.0


def _to_int(val, default=0) -> int:
    if val is None:
        return default
    try:
        v = int(float(str(val)))
        return v if v != 65535 else default
    except (ValueError, TypeError):
        return default


def _to_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        v = float(str(val))
        return v if v != 65535.0 else default
    except (ValueError, TypeError):
        return default


def _get_row(sheet, row_idx):
    return [sheet.cell_value(row_idx, c) for c in range(sheet.ncols)]


def _parse_direction(hex_val) -> str:
    """解析方向码: 0x55=up(上行), 0xaa=down(下行)  依据接口协议"""
    text = str(hex_val).strip().lower()
    if text in ("0x55", "85"):
        return "up"
    if text in ("0xaa", "170"):
        return "down"
    return ""


def build_station_by_platform(station_sheet) -> dict[int, int]:
    """根据车站表建立 platform_id → station_id 映射"""
    mapping = {}
    for r in range(4, station_sheet.nrows):
        row = _get_row(station_sheet, r)
        station_id = _to_int(row[0])
        if station_id == 0:
            continue
        for c in range(3, min(13, station_sheet.ncols)):
            platform_id = _to_int(row[c])
            if platform_id > 0:
                mapping[platform_id] = station_id
    return mapping


def import_segments(cur, sheet):
    """导入 Seg表"""
    count = 0
    for r in range(3, sheet.nrows):
        row = _get_row(sheet, r)
        seg_id = _to_int(row[0])
        if seg_id == 0:
            continue
        length_cm = _to_float(row[1])
        cur.execute("""
            INSERT OR IGNORE INTO segments
            (seg_id, length, start_ep_type, start_ep_id,
             end_ep_type, end_ep_id, start_neighbor, start_lateral,
             end_neighbor, end_lateral)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            seg_id, length_cm / 100.0,
            _to_int(row[2]), _to_int(row[3]),
            _to_int(row[4]), _to_int(row[5]),
            _to_int(row[6]), _to_int(row[7]),
            _to_int(row[8]), _to_int(row[9]),
        ))
        count += 1
    return count


def import_stations(cur, sheet):
    """导入车站表"""
    count = 0
    for r in range(4, sheet.nrows):
        row = _get_row(sheet, r)
        sid = _to_int(row[0])
        if sid == 0:
            continue
        name = str(row[1]).strip()
        if not name:
            continue
        cur.execute("""
            INSERT INTO stations (station_id, name, position)
            VALUES (?, ?, ?)
            ON CONFLICT(station_id) DO UPDATE SET
                name = excluded.name
        """, (sid, name, 0.0))
        count += 1
    return count


def import_platforms(cur, sheet, station_by_platform=None):
    """导入站台表"""
    station_by_platform = station_by_platform or {}
    count = 0
    for r in range(4, sheet.nrows):
        row = _get_row(sheet, r)
        pid = _to_int(row[0])
        if pid == 0:
            continue
        pos = _parse_km(row[1])
        seg_id = _to_int(row[2])
        direction = "up" if str(row[3]).strip().lower() in ("0x55", "85") else "down"
        station_id = station_by_platform.get(pid, 0)
        cur.execute("""
            INSERT INTO platforms
            (platform_id, position, seg_id, direction, station_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(platform_id) DO UPDATE SET
                position = excluded.position,
                seg_id = excluded.seg_id,
                direction = excluded.direction,
                station_id = excluded.station_id
        """, (pid, pos, seg_id, direction, station_id))
        count += 1
    return count


def update_station_positions(cur):
    """用所属站台的最小非零公里标回填车站位置"""
    cur.execute("""
        UPDATE stations
        SET position = COALESCE((
            SELECT MIN(platforms.position)
            FROM platforms
            WHERE platforms.station_id = stations.station_id
              AND platforms.position > 0
        ), position)
    """)


def import_speed_limits(cur, sheet):
    """导入静态限速表"""
    count = 0
    for r in range(3, sheet.nrows):
        row = _get_row(sheet, r)
        idx = _to_int(row[0])
        if idx == 0:
            continue
        seg_id = _to_int(row[1])
        start_cm = _to_float(row[2])
        end_cm = _to_float(row[3])
        speed_cm_s = _to_float(row[5])
        cur.execute("""
            INSERT OR IGNORE INTO speed_limits
            (limit_id, seg_id, start_offset, end_offset, speed_limit)
            VALUES (?, ?, ?, ?, ?)
        """, (idx, seg_id, start_cm / 100.0, end_cm / 100.0, speed_cm_s / 100.0))
        count += 1
    return count


def import_gradients(cur, sheet):
    """导入坡度表"""
    count = 0
    for r in range(3, sheet.nrows):
        row = _get_row(sheet, r)
        idx = _to_int(row[0])
        if idx == 0:
            continue
        start_seg = _to_int(row[1])
        start_cm = _to_float(row[2])
        end_seg = _to_int(row[3])
        end_cm = _to_float(row[4])
        grad_val = _to_float(row[11]) / 10.0
        direction = "down" if str(row[12]).strip().lower() in ("0x55", "85") else "up"

        # 起终点在同一 seg
        segs = [(start_seg, start_cm, end_cm)]
        # 如果终点在不同 seg，拆分
        if end_seg != start_seg and end_seg > 0:
            segs = [(start_seg, start_cm, _to_float(row[4])),
                    (end_seg, 0.0, end_cm)]

        for seg_id, s_off, e_off in segs:
            cur.execute("""
                INSERT INTO gradients
                (gradient_id, seg_id, start_offset, end_offset, gradient, direction)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gradient_id) DO UPDATE SET
                    seg_id = excluded.seg_id,
                    start_offset = excluded.start_offset,
                    end_offset = excluded.end_offset,
                    gradient = excluded.gradient,
                    direction = excluded.direction
            """, (count, seg_id, s_off / 100.0, e_off / 100.0, grad_val, direction))
            count += 1
    return count


def import_signals(cur, sheet):
    """导入信号机表"""
    count = 0
    for r in range(4, sheet.nrows):
        row = _get_row(sheet, r)
        idx = _to_int(row[0])
        if idx == 0:
            continue
        name = str(row[1]).strip()
        if not name:
            continue
        sig_type = str(_to_int(row[2])).strip()
        seg_id = _to_int(row[4])
        if seg_id == 0:
            continue
        offset_cm = _to_float(row[5])
        direction = _parse_direction(row[6])

        # 同名信号机按 Excel 最新数据更新，避免旧列号导入造成错位。
        cur.execute("""
            INSERT INTO signals
            (signal_id, seg_id, offset, signal_type, direction)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                seg_id = excluded.seg_id,
                offset = excluded.offset,
                signal_type = excluded.signal_type,
                direction = excluded.direction
        """, (name, seg_id, offset_cm / 100.0, sig_type, direction))
        count += 1
    return count


def main():
    try:
        import xlrd
    except ImportError:
        print("请先安装 xlrd: pip install xlrd")
        sys.exit(1)

    if not os.path.exists(XLS_PATH):
        print(f"找不到文件: {XLS_PATH}")
        sys.exit(1)

    wb = xlrd.open_workbook(XLS_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    sheet_map = {s: wb.sheet_by_name(s) for s in wb.sheet_names()}

    # 导入线路拓扑
    station_by_platform = {}
    if "车站表" in sheet_map:
        station_by_platform = build_station_by_platform(sheet_map["车站表"])

    if "Seg表" in sheet_map:
        n = import_segments(cur, sheet_map["Seg表"])
        print(f"segments: {n} 条")
    if "车站表" in sheet_map:
        n = import_stations(cur, sheet_map["车站表"])
        print(f"stations: {n} 条")
    if "站台表" in sheet_map:
        n = import_platforms(cur, sheet_map["站台表"], station_by_platform)
        update_station_positions(cur)
        print(f"platforms: {n} 条")
    if "静态限速表" in sheet_map:
        n = import_speed_limits(cur, sheet_map["静态限速表"])
        print(f"speed_limits: {n} 条")
    if "坡度表" in sheet_map:
        n = import_gradients(cur, sheet_map["坡度表"])
        print(f"gradients: {n} 条")
    if "信号机表" in sheet_map:
        n = import_signals(cur, sheet_map["信号机表"])
        print(f"signals: {n} 条")

    conn.commit()
    conn.close()
    wb.release_resources()
    print(f"\n导入完成！数据库: {DB_PATH}")


if __name__ == "__main__":
    main()
