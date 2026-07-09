"""导出前端展示用线路数据 JSON（增强版）。

使用 src.track.loader.TrackLoader 加载完整 TrackData，
包含 BFS 计算后的 segment 绝对坐标、信号机位置、道岔分支层级。
"""
import json
import os
import sys
from collections import deque

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

from src.track.loader import TrackLoader

OUTPUT_PATH = os.path.join(ROOT_DIR, "web", "public", "track-data.json")
NULL_VALUE = 65535


def _trace_track(seg_map, start_id, visited, level):
    """沿 forward 邻居追踪一股轨道，不走 lateral。
    返回本次访问的段 ID 集合。
    """
    q = deque([start_id])
    visited.add(start_id)
    while q:
        sid = q.popleft()
        seg = seg_map.get(sid)
        if not seg:
            continue
        for nid in (seg.start_neighbor, seg.end_neighbor):
            if nid <= 0 or nid == NULL_VALUE or nid not in seg_map:
                continue
            if nid not in visited:
                visited.add(nid)
                q.append(nid)


def compute_branch_levels(segments):
    """双线轨道拓扑：两股主轨 + 渡线 + 支线。

    策略：
      1. 沿 forward 邻居追踪第一股道 → level 0
      2. 从 level 0 的 lateral 邻居中找第二股道的入口 → level 1
      3. 剩余未访问的段按连接关系归入更高层级
    """
    seg_map = {s.seg_id: s for s in segments}
    branch_levels: dict[int, int] = {}

    # 找根段
    referenced = set()
    for s in segments:
        for n in (s.start_neighbor, s.end_neighbor):
            if n > 0 and n != NULL_VALUE:
                referenced.add(n)
    root_id = None
    for s in segments:
        if s.seg_id not in referenced:
            root_id = s.seg_id
            break
    if root_id is None and segments:
        root_id = segments[0].seg_id

    visited: set[int] = set()

    # ── 第一股道（level 0）：只沿 forward 邻居走 ──
    _trace_track(seg_map, root_id, visited, 0)
    for sid in visited:
        branch_levels[sid] = 0

    # ── 找第二股道的入口：level 0 段的 lateral 邻居 ──
    track1_starts = []
    for sid in list(visited):
        seg = seg_map.get(sid)
        if not seg:
            continue
        for nid in (seg.start_lateral, seg.end_lateral):
            if nid > 0 and nid != NULL_VALUE and nid in seg_map and nid not in visited:
                track1_starts.append(nid)

    # ── 第二股道（level 1）──
    if track1_starts:
        # 从第一个入口开始追踪第二股道
        _trace_track(seg_map, track1_starts[0], visited, 1)
        for sid in visited:
            if sid not in branch_levels:
                branch_levels[sid] = 1

        # 第二股道可能断开（通过渡线连接），从其他入口继续追踪
        for start_id in track1_starts:
            if start_id not in visited:
                _trace_track(seg_map, start_id, visited, 1)
                for sid in visited:
                    if sid not in branch_levels:
                        branch_levels[sid] = 1

    # ── 剩余段（渡线短连段 / 真正支线）：按连接关系归入更高层级 ──
    # 收集所有未访问段，按 BFS 从已访问段出发分配层级
    remaining_starts = []
    for sid, seg in seg_map.items():
        if sid not in visited:
            # 找到已访问邻居中层级最低的
            min_level = 999
            for nid in (seg.start_neighbor, seg.end_neighbor,
                        seg.start_lateral, seg.end_lateral):
                if nid in branch_levels:
                    min_level = min(min_level, branch_levels[nid])
            if min_level < 999:
                branch_levels[sid] = min_level + 1
                remaining_starts.append(sid)

    # BFS 继续分配剩余段
    q = deque(remaining_starts)
    visited.update(remaining_starts)
    while q:
        sid = q.popleft()
        seg = seg_map.get(sid)
        if not seg:
            continue
        cur_level = branch_levels.get(sid, 2)
        for nid in (seg.start_neighbor, seg.end_neighbor,
                    seg.start_lateral, seg.end_lateral):
            if nid <= 0 or nid == NULL_VALUE or nid not in seg_map:
                continue
            if nid not in branch_levels:
                branch_levels[nid] = cur_level
                if nid not in visited:
                    visited.add(nid)
                    q.append(nid)
            elif branch_levels[nid] > cur_level + 1:
                branch_levels[nid] = cur_level + 1

    # ── 封顶：level >= 2 统一为 2（渡线/支线）──
    for sid in branch_levels:
        if branch_levels[sid] >= 2:
            branch_levels[sid] = 2

    return branch_levels


def main():
    # 1. 加载 Excel 完整线路数据（含 BFS 坐标计算）
    xls_path = os.path.join(ROOT_DIR, "resource", "线路数据(1).xls")
    loader = TrackLoader()
    td = loader.load_from_excel(xls_path)

    # 2. 计算分支层级
    branch_levels = compute_branch_levels(td.segments)

    # 3. 序列化 segments（含绝对坐标和分支层级）
    segments_json = []
    for seg in td.segments:
        segments_json.append({
            "id": seg.seg_id,
            "length": round(seg.length, 3),
            "absStart": round(seg.abs_start, 3),
            "absEnd": round(seg.abs_start + seg.length, 3),
            "startNeighbor": seg.start_neighbor if seg.start_neighbor != NULL_VALUE else 0,
            "endNeighbor": seg.end_neighbor if seg.end_neighbor != NULL_VALUE else 0,
            "startLateral": seg.start_lateral if seg.start_lateral != NULL_VALUE else 0,
            "endLateral": seg.end_lateral if seg.end_lateral != NULL_VALUE else 0,
            "branchLevel": branch_levels.get(seg.seg_id, 0),
            "hasBranch": bool(
                (seg.start_lateral > 0 and seg.start_lateral != NULL_VALUE) or
                (seg.end_lateral > 0 and seg.end_lateral != NULL_VALUE)
            ),
        })

    # 4. 序列化 signals
    signals_json = []
    for sig in td.signals:
        seg = td._seg_map.get(sig.seg_id)
        signals_json.append({
            "id": sig.signal_id,
            "segId": sig.seg_id,
            "position": round(sig.position, 3),
            "offset": round(sig.offset, 3),
            "direction": sig.direction,
            "branchLevel": branch_levels.get(sig.seg_id, 0),
        })

    # 5. 序列化 stations
    stations_json = []
    for st in td.stations:
        stations_json.append({
            "id": st.station_id,
            "name": st.name,
            "position": round(st.position, 3),
            "platformIds": st.platform_ids,
        })

    # 6. 序列化 platforms
    platforms_json = []
    for pf in td.platforms:
        platforms_json.append({
            "id": pf.platform_id,
            "stationName": pf.station_name,
            "position": round(pf.position, 3),
            "segId": pf.seg_id,
            "direction": pf.direction,
        })

    # 7. 序列化 speed_limits
    speed_limits_json = []
    for sl in td.speed_limits:
        speed_limits_json.append({
            "id": sl.seg_id,  # speed limit linked to segment
            "segId": sl.seg_id,
            "absStart": round(sl.abs_start, 3),
            "absEnd": round(sl.abs_end, 3),
            "speedMs": round(sl.speed_limit, 3),
            "speedKmh": round(sl.speed_limit * 3.6, 1),
        })

    # 8. 序列化 gradients
    gradients_json = []
    for g in td.gradients:
        gradients_json.append({
            "id": g.seg_id,  # gradient linked to segment
            "segId": g.seg_id,
            "absStart": round(g.abs_start, 3),
            "absEnd": round(g.abs_end, 3),
            "gradientPermille": round(g.gradient, 2),
            "direction": g.direction,
        })

    # 9. 构建 routes（按方向排序的站台序列）
    down_platforms = [p for p in td.platforms if p.direction == "down"]
    up_platforms = [p for p in td.platforms if p.direction == "up"]
    down_platforms.sort(key=lambda p: p.position)
    up_platforms.sort(key=lambda p: p.position, reverse=True)

    routes = {
        "down": [{"stationName": p.station_name, "mileage": round(p.position, 3), "segmentId": p.seg_id}
                 for p in down_platforms],
        "up": [{"stationName": p.station_name, "mileage": round(p.position, 3), "segmentId": p.seg_id}
               for p in up_platforms],
    }

    # 10. 汇总
    total_length = td.total_length()
    max_branch_level = max(branch_levels.values()) if branch_levels else 0

    branch_seg_ids = [sid for sid, lv in branch_levels.items() if lv > 0]

    payload = {
        "line": {
            "name": "轨道交通模拟线路",
            "stationCount": len(td.stations),
            "platformCount": len(td.platforms),
            "segmentCount": len(td.segments),
            "signalCount": len(td.signals),
            "switchCount": sum(1 for s in td.segments if (
                (s.start_lateral > 0 and s.start_lateral != NULL_VALUE) or
                (s.end_lateral > 0 and s.end_lateral != NULL_VALUE)
            )),
            "totalLength": round(total_length, 3),
            "maxBranchLevel": max_branch_level,
        },
        "stations": stations_json,
        "platforms": platforms_json,
        "routes": routes,
        "segments": segments_json,
        "signals": signals_json,
        "speedLimits": speed_limits_json,
        "gradients": gradients_json,
        "branchLevels": {str(k): v for k, v in branch_levels.items()},
        "branchSegmentIds": branch_seg_ids,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"已导出: {OUTPUT_PATH}")
    print(f"  车站 {len(td.stations)} 个")
    print(f"  站台 {len(td.platforms)} 个")
    print(f"  区段 {len(td.segments)} 段（含坐标 + 分支层级）")
    print(f"  信号机 {len(td.signals)} 个（含绝对位置）")
    print(f"  限速段 {len(td.speed_limits)} 个")
    print(f"  坡度段 {len(td.gradients)} 个")
    print(f"  分支层级: 最大 {max_branch_level} 级")
    print(f"  线路总长: {total_length:.1f} m")


if __name__ == "__main__":
    main()
