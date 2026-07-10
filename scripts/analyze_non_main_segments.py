"""分析非主干线路 Seg 的设备属性。

从原始 Excel 与拓扑分类结果中交叉提取：
  - Seg 端点类型、轨道区段属性、长度
  - 点表侧线描述
  - 道岔、车档、车库门、SPKS、信号机命中情况

输出用于判断主线外 211 个 Seg 更像车辆段、折返线、旁线还是联络线。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import xlrd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLS = ROOT / "resource" / "线路数据(1).xls"
DEFAULT_CLASSIFICATION = ROOT / "resource" / "topology_classification" / "track_topology_classification.json"
DEFAULT_OUT_DIR = ROOT / "resource" / "topology_classification"


def to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def cell_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        return text[:-2]
    return text


def read_sheet_rows(wb, index: int) -> list[list]:
    sheet = wb.sheet_by_index(index)
    return [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(4, sheet.nrows)]


def load_excel_maps(xls_path: Path) -> dict:
    """读取关键 Sheet，按 Seg 或设备引用建立索引。"""
    wb = xlrd.open_workbook(str(xls_path))

    points = {}
    for row in read_sheet_rows(wb, 1):
        point_id = to_int(row[0])
        if point_id:
            points[point_id] = {
                "name": cell_text(row[1]),
                "km_cm": to_int(row[3]),
                "point_type": to_int(row[4]),
                "side_desc": cell_text(row[13]) if len(row) > 13 else "",
            }

    segs = {}
    for row in read_sheet_rows(wb, 2):
        seg_id = to_int(row[0])
        if seg_id:
            segs[seg_id] = {
                "length_m": to_int(row[1]) / 100.0,
                "start_ep_type": to_int(row[2]),
                "start_ep_id": to_int(row[3]),
                "end_ep_type": to_int(row[4]),
                "end_ep_id": to_int(row[5]),
                "start_neighbor": to_int(row[6]),
                "start_lateral": to_int(row[7]),
                "end_neighbor": to_int(row[8]),
                "end_lateral": to_int(row[9]),
                "spks_count": to_int(row[13]),
                "spks_ids": [to_int(row[c]) for c in range(14, 18) if c < len(row) and to_int(row[c])],
                "track_attr": cell_text(row[23]) if len(row) > 23 else "",
            }

    switches_by_seg: dict[int, list[str]] = {}
    for row in read_sheet_rows(wb, 7):
        switch_id = to_int(row[0])
        name = cell_text(row[1])
        if not switch_id:
            continue
        for label, col in (("normal", 4), ("reverse", 5), ("merge", 6)):
            seg_id = to_int(row[col])
            if seg_id:
                switches_by_seg.setdefault(seg_id, []).append(f"{name or switch_id}:{label}")

    signals_by_seg: dict[int, list[str]] = {}
    for row in read_sheet_rows(wb, 8):
        name = cell_text(row[1])
        seg_id = to_int(row[4])
        if seg_id:
            signals_by_seg.setdefault(seg_id, []).append(name)

    buffers_by_seg: dict[int, list[str]] = {}
    for row in read_sheet_rows(wb, 27):
        buffer_id = to_int(row[0])
        seg_id = to_int(row[1])
        buffer_type = to_int(row[3])
        if seg_id:
            buffers_by_seg.setdefault(seg_id, []).append(f"{buffer_id}:type{buffer_type}")

    spks_by_seg: dict[int, list[str]] = {}
    for row in read_sheet_rows(wb, 30):
        spks_id = to_int(row[0])
        desc = cell_text(row[1])
        seg_id = to_int(row[2])
        if seg_id:
            spks_by_seg.setdefault(seg_id, []).append(f"{spks_id}:{desc}")

    garage_by_seg: dict[int, list[str]] = {}
    for row in read_sheet_rows(wb, 31):
        door_id = to_int(row[0])
        seg_id = to_int(row[1])
        door_attr = to_int(row[4])
        in_routes = [to_int(row[c]) for c in range(6, 16) if c < len(row) and to_int(row[c])]
        out_routes = [to_int(row[c]) for c in range(17, 27) if c < len(row) and to_int(row[c])]
        if seg_id:
            attr_name = "库线" if door_attr == 1 else "洗车库" if door_attr == 2 else f"属性{door_attr}"
            garage_by_seg.setdefault(seg_id, []).append(
                f"{door_id}:{attr_name}:in{len(in_routes)}:out{len(out_routes)}"
            )

    return {
        "points": points,
        "segs": segs,
        "switches_by_seg": switches_by_seg,
        "signals_by_seg": signals_by_seg,
        "buffers_by_seg": buffers_by_seg,
        "spks_by_seg": spks_by_seg,
        "garage_by_seg": garage_by_seg,
    }


def infer_branch_role(branch: dict, rows: list[dict]) -> str:
    """基于设备命中和拓扑形态给出保守分类。"""
    garage = sum(1 for row in rows if row["garage_doors"])
    buffers = sum(1 for row in rows if row["buffers"])
    spks = sum(1 for row in rows if row["spks"])
    switches = sum(1 for row in rows if row["switches"])
    cycles = branch["cycle_count"]
    dead = branch["dead_end_count"]

    if garage:
        return "车辆段/库线/洗车库相关"
    if buffers >= 2 and spks >= 2 and dead >= 2:
        return "停车线/尽端线/场段相关"
    if cycles > 0 and switches >= 4:
        return "旁路/联络线/渡线群"
    if dead > 0:
        return "折返/尽端辅助线"
    return "普通联络或辅助线"


def analyze(xls_path: Path, classification_path: Path, out_dir: Path) -> None:
    data = json.loads(classification_path.read_text(encoding="utf-8"))
    maps = load_excel_maps(xls_path)
    segs = maps["segs"]
    points = maps["points"]

    detail_rows = []
    summary_rows = []

    for branch in data["branches"]:
        branch_rows = []
        total_length = 0.0
        ep_types = Counter()
        track_attrs = Counter()
        side_descs = Counter()

        for seg_id in branch["seg_ids"]:
            seg = segs.get(seg_id, {})
            total_length += seg.get("length_m", 0.0)
            ep_types.update([seg.get("start_ep_type", 0), seg.get("end_ep_type", 0)])
            if seg.get("track_attr"):
                track_attrs.update([seg["track_attr"]])

            endpoint_descs = []
            for point_id in (seg.get("start_ep_id", 0), seg.get("end_ep_id", 0)):
                desc = points.get(point_id, {}).get("side_desc", "")
                if desc:
                    endpoint_descs.append(desc)
                    side_descs.update([desc])

            row = {
                "branch_id": branch["branch_id"],
                "branch_type": branch["type"],
                "seg_id": seg_id,
                "length_m": seg.get("length_m", 0.0),
                "start_ep_type": seg.get("start_ep_type", ""),
                "start_ep_id": seg.get("start_ep_id", ""),
                "end_ep_type": seg.get("end_ep_type", ""),
                "end_ep_id": seg.get("end_ep_id", ""),
                "track_attr": seg.get("track_attr", ""),
                "endpoint_side_desc": " | ".join(sorted(set(endpoint_descs))),
                "switches": " | ".join(maps["switches_by_seg"].get(seg_id, [])),
                "signals": " | ".join(maps["signals_by_seg"].get(seg_id, [])),
                "buffers": " | ".join(maps["buffers_by_seg"].get(seg_id, [])),
                "spks": " | ".join(maps["spks_by_seg"].get(seg_id, [])),
                "garage_doors": " | ".join(maps["garage_by_seg"].get(seg_id, [])),
            }
            detail_rows.append(row)
            branch_rows.append(row)

        inferred = infer_branch_role(branch, branch_rows)
        summary_rows.append(
            {
                "branch_id": branch["branch_id"],
                "seg_count": branch["seg_count"],
                "total_length_m": round(total_length, 1),
                "connectors": " ".join(map(str, branch["connectors"])),
                "dead_end_count": branch["dead_end_count"],
                "cycle_count": branch["cycle_count"],
                "switch_seg_count": sum(1 for row in branch_rows if row["switches"]),
                "signal_seg_count": sum(1 for row in branch_rows if row["signals"]),
                "buffer_stop_seg_count": sum(1 for row in branch_rows if row["buffers"]),
                "spks_seg_count": sum(1 for row in branch_rows if row["spks"]),
                "garage_door_seg_count": sum(1 for row in branch_rows if row["garage_doors"]),
                "top_endpoint_side_desc": " | ".join(f"{k}:{v}" for k, v in side_descs.most_common(8)),
                "track_attrs": " | ".join(f"{k}:{v}" for k, v in track_attrs.most_common(8)),
                "endpoint_types": " | ".join(f"{k}:{v}" for k, v in ep_types.most_common()),
                "inferred_role": inferred,
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "non_main_branch_device_summary.csv"
    detail_path = out_dir / "non_main_segment_device_detail.csv"
    json_path = out_dir / "non_main_branch_device_summary.json"

    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)

    with detail_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0]))
        writer.writeheader()
        writer.writerows(detail_rows)

    json_path.write_text(json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"输出: {summary_path}")
    print(f"输出: {detail_path}")
    print(f"输出: {json_path}")
    for row in summary_rows:
        print(row["branch_id"], row["seg_count"], row["inferred_role"], "garage", row["garage_door_seg_count"], "buffer", row["buffer_stop_seg_count"], "spks", row["spks_seg_count"])


def main() -> None:
    parser = argparse.ArgumentParser(description="分析主线外 Seg 的设备属性")
    parser.add_argument("--xls", type=Path, default=DEFAULT_XLS, help="原始线路 Excel")
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION, help="拓扑分类 JSON")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="输出目录")
    args = parser.parse_args()
    analyze(args.xls, args.classification, args.out_dir)


if __name__ == "__main__":
    main()
