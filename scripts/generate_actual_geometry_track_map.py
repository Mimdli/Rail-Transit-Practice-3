"""按原始点表/Seg表生成尽量贴近实际的线路几何示意图。

口径:
  - 横坐标优先使用点表公里标。
  - 纵坐标使用上下行与侧线描述分层。
  - 道岔端点没有公里标时，根据相邻已知点迭代求平均位置。
  - 输出黑底联锁风格图，展示实际分岔方向与交叉关系。
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import xlrd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLS = ROOT / "resource" / "线路数据(1).xls"
DEFAULT_DB = ROOT / "data" / "railway.db"
DEFAULT_OUT = ROOT / "resource" / "actual_geometry_track_map"

BLUE = "#76a7f0"
GREEN = "#00ff33"
RED = "#ff2020"
GREY = "#969696"
WHITE = "#e8eef8"
YELLOW = "#d8e600"


def to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_points_and_segments(xls_path: Path) -> tuple[dict, dict]:
    """读取点表和 Seg 表。"""
    wb = xlrd.open_workbook(str(xls_path))
    point_sheet = wb.sheet_by_index(1)
    seg_sheet = wb.sheet_by_index(2)

    points = {}
    for r in range(4, point_sheet.nrows):
        row = [point_sheet.cell_value(r, c) for c in range(point_sheet.ncols)]
        point_id = to_int(row[0])
        if not point_id:
            continue
        direction = to_int(row[12])
        side_desc = to_int(row[13], 0)
        track_name = str(row[2]).strip()
        km_m = to_float(row[3]) / 100.0
        points[point_id] = {
            "name": str(row[1]).strip(),
            "track_name": track_name,
            "km_m": km_m,
            "direction": direction,
            "side_desc": side_desc,
        }

    segments = {}
    for r in range(4, seg_sheet.nrows):
        row = [seg_sheet.cell_value(r, c) for c in range(seg_sheet.ncols)]
        seg_id = to_int(row[0])
        if not seg_id:
            continue
        segments[seg_id] = {
            "seg_id": seg_id,
            "length_m": to_float(row[1]) / 100.0,
            "start_ep": (to_int(row[2]), to_int(row[3])),
            "end_ep": (to_int(row[4]), to_int(row[5])),
            "start_neighbor": to_int(row[6]),
            "start_lateral": to_int(row[7]),
            "end_neighbor": to_int(row[8]),
            "end_lateral": to_int(row[9]),
            "track_attr": str(row[23]).strip() if len(row) > 23 else "",
        }

    return points, segments


def point_y(point: dict) -> float:
    """根据上下行和侧线描述给点分层。"""
    direction = point["direction"]
    side = point["side_desc"]
    track = point["track_name"].upper()

    base = 1.0 if direction == 0 else -1.0
    # 侧线描述越大，离正线越远；下行往上，上行往下。
    y = base * (1.0 + side * 0.34)

    # 库线/场段股道编号通常较多，再稍微扩展开。
    digits = "".join(ch for ch in track if ch.isdigit())
    if digits:
        n = int(digits)
        if n >= 10:
            y += base * min((n - 8) * 0.06, 1.2)
    return y


def endpoint_key(ep: tuple[int, int]) -> tuple[int, int]:
    return ep


def build_endpoint_positions(points: dict, segments: dict) -> dict[tuple[int, int], tuple[float, float]]:
    """给点端点和道岔端点都求出坐标。"""
    positions: dict[tuple[int, int], tuple[float, float]] = {}
    for point_id, point in points.items():
        positions[(2, point_id)] = (point["km_m"], point_y(point))

    # 道岔端点(type=3)无直接公里标，用相邻已知端点迭代平均。
    for _ in range(20):
        changed = False
        buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for seg in segments.values():
            a = endpoint_key(seg["start_ep"])
            b = endpoint_key(seg["end_ep"])
            if a in positions and b not in positions:
                buckets.setdefault(b, []).append(positions[a])
            if b in positions and a not in positions:
                buckets.setdefault(a, []).append(positions[b])

        for key, coords in buckets.items():
            if key in positions or not coords:
                continue
            x = sum(coord[0] for coord in coords) / len(coords)
            y = sum(coord[1] for coord in coords) / len(coords)
            positions[key] = (x, y)
            changed = True
        if not changed:
            break

    return positions


def load_signal_segs(db_path: Path) -> set[int]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT seg_id FROM signals WHERE seg_id > 0")
    result = {row[0] for row in cur.fetchall()}
    conn.close()
    return result


def load_stations(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT station_id, name, position FROM stations ORDER BY position, station_id")
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result


def draw_station_labels(ax, stations: list[dict], x_min: float, x_max: float) -> None:
    """用数据库车站公里标标注车站；position=0 的站用主线起点附近均匀放置。"""
    zero_stations = [station for station in stations if abs(station["position"]) < 1e-6]
    real_stations = [station for station in stations if abs(station["position"]) >= 1e-6]

    zero_x0 = max(x_min, -0.2)
    for idx, station in enumerate(zero_stations):
        x = zero_x0 + idx * 0.75
        ax.text(x, 4.9, station["name"], color=YELLOW, fontsize=11, ha="center", va="bottom")
        ax.plot([x, x], [3.9, 4.65], color=GREY, lw=0.7, ls="--", zorder=0)

    for station in real_stations:
        x = station["position"] / 1000.0
        if x_min - 1 <= x <= x_max + 1:
            ax.text(x, 4.9, station["name"], color=GREEN, fontsize=11, ha="center", va="bottom")
            ax.plot([x, x], [3.9, 4.65], color=GREY, lw=0.7, ls="--", zorder=0)


def draw_actual_geometry(xls_path: Path, db_path: Path, out_base: Path) -> None:
    points, segments = load_points_and_segments(xls_path)
    positions = build_endpoint_positions(points, segments)
    signal_segs = load_signal_segs(db_path)
    stations = load_stations(db_path)

    xs = [pos[0] / 1000.0 for pos in positions.values() if math.isfinite(pos[0])]
    x_min, x_max = min(xs), max(xs)

    def render(path_base: Path, view_min: float, view_max: float, title: str, figsize=(32, 12)) -> int:
        fig, ax = plt.subplots(figsize=figsize, dpi=180)
        fig.patch.set_facecolor("black")
        ax.set_facecolor("black")
        ax.axis("off")

        used_seg_count = 0
        for seg_id, seg in segments.items():
            a = endpoint_key(seg["start_ep"])
            b = endpoint_key(seg["end_ep"])
            if a not in positions or b not in positions:
                continue
            x1, y1 = positions[a]
            x2, y2 = positions[b]
            kx1, kx2 = x1 / 1000.0, x2 / 1000.0
            if max(kx1, kx2) < view_min or min(kx1, kx2) > view_max:
                continue
            used_seg_count += 1
            is_lateral = seg["start_lateral"] not in (0, 65535) or seg["end_lateral"] not in (0, 65535)
            lw = 1.55 if is_lateral else 1.05
            ax.plot([kx1, kx2], [y1, y2], color=BLUE, lw=lw, solid_capstyle="round", zorder=1)

            mx, my = (kx1 + kx2) / 2, (y1 + y2) / 2
            if view_min <= mx <= view_max:
                if seg_id in signal_segs:
                    ax.scatter([mx], [my + 0.08], s=16, color=RED if seg_id % 2 else GREEN, zorder=4)
                if is_lateral or seg_id in signal_segs or seg_id % 2 == 0:
                    ax.text(mx, my + 0.11, str(seg_id), color=GREEN, fontsize=4.8, ha="center", va="bottom", zorder=5)

        draw_station_labels(ax, stations, view_min, view_max)
        ax.text(view_min, 5.55, title, color=WHITE, fontsize=15, ha="left")
        ax.text(
            view_min,
            5.25,
            f"Point km + up/down + siding layer | visible Seg {used_seg_count}",
            color=WHITE,
            fontsize=8,
            ha="left",
        )
        ax.text(
            view_max,
            -5.4,
            "X=km  Y=up/down+siding  Blue=track  Green=Seg ID  Red/green=signal Seg",
            color=WHITE,
            fontsize=8,
            ha="right",
        )

        for km in range(math.floor(view_min), math.ceil(view_max) + 1):
            ax.plot([km, km], [-5.0, -4.85], color=GREY, lw=0.6)
            ax.text(km, -5.12, f"{km}km", color=WHITE, fontsize=5, ha="center", va="top")

        ax.set_xlim(view_min, view_max)
        ax.set_ylim(-5.6, 5.9)
        path_base.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path_base.with_suffix(".png"), bbox_inches="tight", facecolor="black")
        fig.savefig(path_base.with_suffix(".svg"), bbox_inches="tight", facecolor="black")
        plt.close(fig)
        return used_seg_count

    used_seg_count = render(out_base, x_min - 0.5, x_max + 0.5, "Actual-ish Geometry Track Map")

    # 分段图更适合核对具体岔线方向。
    section_dir = out_base.parent / "actual_geometry_sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    ranges = [(-2.5, 1.0), (1.0, 4.0), (4.0, 7.0), (7.0, 10.0), (10.0, 13.0), (13.0, 17.0)]
    for idx, (left, right) in enumerate(ranges, 1):
        left_name = f"{int(left * 1000):+d}m".replace("+", "p").replace("-", "m")
        right_name = f"{int(right * 1000):+d}m".replace("+", "p").replace("-", "m")
        render(section_dir / f"actual_geometry_section_{idx}_{left_name}_to_{right_name}", left, right, f"Actual Geometry Section {idx}: {left:g}km - {right:g}km", figsize=(18, 9))

    print(f"输出: {out_base.with_suffix('.png')}")
    print(f"输出: {out_base.with_suffix('.svg')}")
    print(f"分段输出目录: {out_base.parent / 'actual_geometry_sections'}")
    print(f"Seg positioned: {used_seg_count}/{len(segments)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="按原始公里标和端点关系生成线路几何图")
    parser.add_argument("--xls", type=Path, default=DEFAULT_XLS, help="原始线路 Excel")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出文件基名，不带扩展名")
    args = parser.parse_args()
    draw_actual_geometry(args.xls, args.db, args.out)


if __name__ == "__main__":
    main()
