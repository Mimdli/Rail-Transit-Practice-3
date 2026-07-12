"""ATS 线路图专用的原始道岔和信号机布局数据。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


DATA_PATH = Path(__file__).resolve().parents[2] / "resource" / "ats_layout.json"


@dataclass(frozen=True)
class AtsSwitch:
    switch_id: int
    name: str
    direction: int
    normal_seg_id: int
    reverse_seg_id: int
    merge_seg_id: int


@dataclass(frozen=True)
class AtsSignal:
    layout_id: int
    name: str
    signal_type: int
    seg_id: int
    offset_m: float
    direction: str


@lru_cache(maxsize=1)
def load_ats_layout() -> tuple[tuple[AtsSwitch, ...], tuple[AtsSignal, ...]]:
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    switches = tuple(AtsSwitch(**item) for item in payload["switches"])
    signals = tuple(AtsSignal(**item) for item in payload["signals"])
    return switches, signals
