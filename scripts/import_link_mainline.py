"""从 Link 公里标工作簿导出上下行主线链。"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "Link公里标最新数据-校对.xlsx"
DEFAULT_OUTPUT = ROOT / "resource" / "link_mainline.json"


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter()
                if node.tag.endswith("}t"))
        for item in root
    ]


def _sheet_rows(archive: zipfile.ZipFile, sheet_number: int,
                strings: list[str]) -> list[dict]:
    root = ET.fromstring(
        archive.read(f"xl/worksheets/sheet{sheet_number}.xml"))
    records = []
    for row in (node for node in root.iter() if node.tag.endswith("}row")):
        cells: dict[str, str | None] = {}
        for cell in (node for node in row if node.tag.endswith("}c")):
            column = re.match(r"[A-Z]+", cell.attrib["r"]).group()
            value = next(
                (node.text for node in cell if node.tag.endswith("}v")), None)
            if value is not None and cell.attrib.get("t") == "s":
                value = strings[int(value)]
            cells[column] = value
        if cells.get("A") in (None, "LinkId"):
            continue
        records.append({
            "link_id": int(float(cells["A"])),
            "start_m": float(cells["B"]) / 100.0,
            "end_m": float(cells["C"]) / 100.0,
            "length_m": float(cells["D"]) / 100.0,
        })
    return records


def load_mainline(source: Path) -> dict:
    """读取 Strict OOXML；不依赖 Excel 特定主题或样式。"""
    with zipfile.ZipFile(source) as archive:
        strings = _shared_strings(archive)
        directions = {
            "up": _sheet_rows(archive, 2, strings),
            "down": _sheet_rows(archive, 3, strings),
        }
    for direction, links in directions.items():
        for left, right in zip(links, links[1:]):
            if abs(left["end_m"] - right["start_m"]) > 1e-6:
                raise ValueError(
                    f"{direction} 主线不连续: Link {left['link_id']} -> "
                    f"{right['link_id']}")
    return {
        "source": source.name,
        "unit": "m",
        "directions": directions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出有序 Link 主线数据")
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = load_mainline(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已导出 {sum(map(len, payload['directions'].values()))} 条主线 Link: "
          f"{args.output}")


if __name__ == "__main__":
    main()
