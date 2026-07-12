"""从原始线路表导出 ATS 线路图需要的道岔和信号机布局数据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "resource" / "线路数据(1).xls"
DEFAULT_OUTPUT = ROOT / "resource" / "ats_layout.json"
NULL_ID = 65535


def _integer(value) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return 0
    return 0 if result == NULL_ID else result


def _direction(value) -> str:
    text = str(value).strip().lower()
    return "down" if text in ("0x55", "85") else "up"


def load_layout(source: Path) -> dict:
    import xlrd

    workbook = xlrd.open_workbook(str(source))
    switch_sheet = workbook.sheet_by_name("道岔表")
    signal_sheet = workbook.sheet_by_name("信号机表")

    switches = []
    for row_index in range(4, switch_sheet.nrows):
        row = switch_sheet.row_values(row_index)
        switch_id = _integer(row[0])
        if not switch_id:
            continue
        switches.append({
            "switch_id": switch_id,
            "name": str(row[1]).strip().removesuffix(".0"),
            "direction": _integer(row[3]),
            "normal_seg_id": _integer(row[4]),
            "reverse_seg_id": _integer(row[5]),
            "merge_seg_id": _integer(row[6]),
        })

    signals = []
    for row_index in range(4, signal_sheet.nrows):
        row = signal_sheet.row_values(row_index)
        source_index = _integer(row[0])
        name = str(row[1]).strip()
        seg_id = _integer(row[4])
        if not source_index or not name or not seg_id:
            continue
        signals.append({
            "layout_id": source_index,
            "name": name,
            "signal_type": _integer(row[2]),
            "seg_id": seg_id,
            "offset_m": float(row[5] or 0.0) / 100.0,
            "direction": _direction(row[6]),
        })

    workbook.release_resources()
    return {
        "source": source.name,
        "unit": "m",
        "switches": switches,
        "signals": signals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 ATS 道岔和信号机布局")
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = load_layout(args.source)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已导出道岔 {len(payload['switches'])} 条、信号机 "
          f"{len(payload['signals'])} 条: {args.output}")


if __name__ == "__main__":
    main()
