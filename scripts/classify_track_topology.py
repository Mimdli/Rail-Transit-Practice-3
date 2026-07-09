"""识别主线并分类分支。

口径:
  1. 每个 Seg 作为一个拓扑点。
  2. 正向邻接成本低，侧向/道岔连接成本高。
  3. 在所有车站锚点之间寻找最长的低成本最短路径，作为主通道。
  4. 移除主通道后，对剩余连通片按接入点、环路、死端进行分类。
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

import networkx as nx
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "railway.db"
DEFAULT_OUT_DIR = ROOT / "resource" / "topology_classification"

sys.path.insert(0, str(ROOT))

from src.track.db_loader import DBLoader


def _valid_neighbor(seg_id: int) -> bool:
    return seg_id is not None and seg_id > 0 and seg_id != 65535


def load_station_anchors(db_path: Path) -> list[dict]:
    """读取每个车站关联的站台 Seg，作为主线识别锚点。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.station_id, s.name, s.position, p.seg_id
        FROM stations s
        JOIN platforms p ON p.station_id = s.station_id
        WHERE p.seg_id IS NOT NULL AND p.seg_id > 0
        ORDER BY s.station_id, p.platform_id
        """
    )
    grouped: dict[int, dict] = {}
    for row in cur.fetchall():
        item = grouped.setdefault(
            row["station_id"],
            {
                "station_id": row["station_id"],
                "name": row["name"],
                "position": row["position"],
                "seg_ids": [],
            },
        )
        if row["seg_id"] not in item["seg_ids"]:
            item["seg_ids"].append(row["seg_id"])
    conn.close()
    return list(grouped.values())


def build_weighted_graph(db_path: Path) -> tuple[nx.Graph, dict[int, float]]:
    """构建带权无向图，侧向连接权重大，避免主线优先走道岔。"""
    td = DBLoader().load_from_db(str(db_path))
    graph = nx.Graph()
    length_by_seg = {}

    for seg in td.segments:
        graph.add_node(seg.seg_id, length=seg.length)
        length_by_seg[seg.seg_id] = seg.length

    for seg in td.segments:
        for attr, edge_type, weight in (
            ("start_neighbor", "forward", 1.0),
            ("end_neighbor", "forward", 1.0),
            ("start_lateral", "lateral", 8.0),
            ("end_lateral", "lateral", 8.0),
        ):
            neighbor = getattr(seg, attr)
            if not _valid_neighbor(neighbor) or neighbor not in graph:
                continue
            if graph.has_edge(seg.seg_id, neighbor):
                old_weight = graph[seg.seg_id][neighbor]["weight"]
                graph[seg.seg_id][neighbor]["weight"] = min(old_weight, weight)
                if graph[seg.seg_id][neighbor]["edge_type"] != edge_type:
                    graph[seg.seg_id][neighbor]["edge_type"] = "mixed"
                continue
            graph.add_edge(
                seg.seg_id,
                neighbor,
                weight=weight,
                edge_type=edge_type,
                source_attr=attr,
            )

    return graph, length_by_seg


def shortest_between_anchor_groups(graph: nx.Graph, left: dict, right: dict) -> tuple[float, list[int]]:
    """返回两个车站锚点集合之间成本最低的路径。"""
    best_cost = float("inf")
    best_path: list[int] = []
    for start in left["seg_ids"]:
        for end in right["seg_ids"]:
            if start not in graph or end not in graph:
                continue
            try:
                path = nx.shortest_path(graph, start, end, weight="weight")
            except nx.NetworkXNoPath:
                continue
            cost = sum(graph[u][v]["weight"] for u, v in zip(path, path[1:]))
            if cost < best_cost:
                best_cost = cost
                best_path = path
    return best_cost, best_path


def identify_main_path(graph: nx.Graph, stations: list[dict]) -> tuple[dict, dict, list[int], float]:
    """用车站锚点间最长低成本路径识别主通道。"""
    best = None
    for i, left in enumerate(stations):
        for right in stations[i + 1 :]:
            cost, path = shortest_between_anchor_groups(graph, left, right)
            if not path:
                continue
            station_hits = count_station_hits(path, stations)
            # 优先覆盖更多车站，再比较路径长度，避免选到短但绕的侧线。
            score = (station_hits, len(path), cost)
            if best is None or score > best["score"]:
                best = {
                    "start_station": left,
                    "end_station": right,
                    "path": path,
                    "cost": cost,
                    "score": score,
                }

    if best is None:
        raise RuntimeError("无法在车站锚点之间找到可用路径")
    return best["start_station"], best["end_station"], best["path"], best["cost"]


def count_station_hits(path: list[int], stations: list[dict]) -> int:
    """统计路径命中的车站数量，站台上下行命中任意一个即算命中。"""
    path_set = set(path)
    return sum(1 for station in stations if path_set.intersection(station["seg_ids"]))


def expand_main_corridor(graph: nx.Graph, main_path: list[int], stations_on_main: list[dict]) -> list[int]:
    """把单条主通道扩展为主线走廊，覆盖上下行平行正线。

    对相邻车站锚点，保留所有成本接近最短路径的连接；侧向连接成本较高，
    因此不会轻易把车辆段或岔线吸收到主线走廊里。
    """
    main_set = set(main_path)
    ordered = [station for station in stations_on_main if station["on_main"]]

    for left, right in zip(ordered, ordered[1:]):
        candidates = []
        for start in left["anchor_seg_ids"]:
            for end in right["anchor_seg_ids"]:
                if start not in graph or end not in graph:
                    continue
                try:
                    path = nx.shortest_path(graph, start, end, weight="weight")
                except nx.NetworkXNoPath:
                    continue
                cost = sum(graph[u][v]["weight"] for u, v in zip(path, path[1:]))
                candidates.append((cost, path))

        if not candidates:
            continue
        min_cost = min(cost for cost, _ in candidates)
        for cost, path in candidates:
            if cost <= min_cost + 2.0:
                main_set.update(path)

    # 按原主通道顺序优先输出，其他走廊段按编号补在后面，便于 CSV 稳定对比。
    ordered_main = list(main_path)
    ordered_main.extend(sorted(main_set - set(main_path)))
    return ordered_main


def classify_branches(graph: nx.Graph, main_path: list[int]) -> list[dict]:
    """移除主通道后分类剩余分支连通片。"""
    main_set = set(main_path)
    branch_graph = graph.copy()
    branch_graph.remove_nodes_from(main_set)

    branches = []
    for idx, nodes in enumerate(sorted(nx.connected_components(branch_graph), key=len, reverse=True), 1):
        sub = branch_graph.subgraph(nodes).copy()
        connectors = sorted(
            {
                main_node
                for node in nodes
                for main_node in graph.neighbors(node)
                if main_node in main_set
            }
        )
        dead_ends = sorted([node for node, degree in sub.degree() if degree <= 1])
        cycle_count = len(nx.cycle_basis(sub))

        if len(connectors) >= 2:
            branch_type = "联络/旁路分支"
        elif cycle_count > 0:
            branch_type = "环形分支"
        elif dead_ends:
            branch_type = "尽端/折返分支"
        else:
            branch_type = "普通分支"

        branches.append(
            {
                "branch_id": idx,
                "type": branch_type,
                "seg_count": len(nodes),
                "connectors": connectors,
                "dead_end_count": len(dead_ends),
                "cycle_count": cycle_count,
                "seg_ids": sorted(nodes),
            }
        )

    return branches


def project_stations_on_main(main_path: list[int], stations: list[dict]) -> list[dict]:
    """按主通道上的位置给车站排序，便于人工检查识别结果。"""
    index_by_seg = {seg_id: index for index, seg_id in enumerate(main_path)}
    projected = []
    for station in stations:
        hits = [index_by_seg[sid] for sid in station["seg_ids"] if sid in index_by_seg]
        projected.append(
            {
                "station_id": station["station_id"],
                "name": station["name"],
                "position": station["position"],
                "main_index": min(hits) if hits else None,
                "anchor_seg_ids": station["seg_ids"],
                "on_main": bool(hits),
            }
        )
    return sorted(projected, key=lambda item: (item["main_index"] is None, item["main_index"] or 999999, item["station_id"]))


def write_outputs(
    out_dir: Path,
    graph: nx.Graph,
    main_start: dict,
    main_end: dict,
    main_path: list[int],
    main_corridor: list[int],
    main_cost: float,
    stations_on_main: list[dict],
    branches: list[dict],
) -> None:
    """输出 JSON 总表和 CSV 明细，方便后续画图或人工核查。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "method": "station_anchor_longest_low_cost_shortest_path",
        "edge_cost": {"forward": 1.0, "lateral": 8.0},
        "total_seg_count": graph.number_of_nodes(),
        "total_edge_count": graph.number_of_edges(),
        "main_start_station": main_start["name"],
        "main_end_station": main_end["name"],
        "main_seg_count": len(main_path),
        "main_corridor_seg_count": len(main_corridor),
        "main_cost": main_cost,
        "branch_count": len(branches),
        "main_seg_ids": main_path,
        "main_corridor_seg_ids": main_corridor,
        "stations": stations_on_main,
        "branches": branches,
    }
    (out_dir / "track_topology_classification.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (out_dir / "main_path_segments.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["seq", "seg_id"])
        for seq, seg_id in enumerate(main_path, 1):
            writer.writerow([seq, seg_id])

    with (out_dir / "main_corridor_segments.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["seq", "seg_id"])
        for seq, seg_id in enumerate(main_corridor, 1):
            writer.writerow([seq, seg_id])

    with (out_dir / "station_projection.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["station_id", "name", "position_m", "on_main", "main_index", "anchor_seg_ids"])
        for station in stations_on_main:
            writer.writerow(
                [
                    station["station_id"],
                    station["name"],
                    station["position"],
                    station["on_main"],
                    station["main_index"],
                    " ".join(str(sid) for sid in station["anchor_seg_ids"]),
                ]
            )

    with (out_dir / "branch_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["branch_id", "type", "seg_count", "connector_seg_ids", "dead_end_count", "cycle_count", "seg_ids"])
        for branch in branches:
            writer.writerow(
                [
                    branch["branch_id"],
                    branch["type"],
                    branch["seg_count"],
                    " ".join(str(sid) for sid in branch["connectors"]),
                    branch["dead_end_count"],
                    branch["cycle_count"],
                    " ".join(str(sid) for sid in branch["seg_ids"]),
                ]
            )


def draw_classification_map(graph: nx.Graph, main_corridor: list[int], branches: list[dict], out_dir: Path) -> None:
    """绘制主线/分支分类图，方便人工快速核对。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    pos = nx.spring_layout(graph, seed=86, k=0.18, iterations=450)
    main_set = set(main_corridor)
    branch_by_seg = {
        seg_id: branch["branch_id"]
        for branch in branches
        for seg_id in branch["seg_ids"]
    }

    fig, ax = plt.subplots(figsize=(30, 22), dpi=180)
    ax.set_title(
        f"主线识别与分支分类：主线走廊 {len(main_corridor)} 个 Seg，分支 {len(branches)} 个",
        fontsize=18,
        pad=18,
    )

    main_edges = []
    branch_edges = []
    cross_edges = []
    for u, v in graph.edges:
        if u in main_set and v in main_set:
            main_edges.append((u, v))
        elif u not in main_set and v not in main_set:
            branch_edges.append((u, v))
        else:
            cross_edges.append((u, v))

    nx.draw_networkx_edges(graph, pos, edgelist=branch_edges, ax=ax, width=0.9, edge_color="#94a3b8", alpha=0.55)
    nx.draw_networkx_edges(graph, pos, edgelist=main_edges, ax=ax, width=2.0, edge_color="#16a34a", alpha=0.9)
    nx.draw_networkx_edges(graph, pos, edgelist=cross_edges, ax=ax, width=1.4, edge_color="#dc2626", style="dashed", alpha=0.8)

    palette = [
        "#f97316", "#8b5cf6", "#06b6d4", "#eab308", "#ec4899",
        "#64748b", "#ef4444", "#14b8a6", "#a855f7", "#84cc16",
    ]
    nx.draw_networkx_nodes(graph, pos, nodelist=list(main_set), ax=ax, node_size=150, node_color="#bbf7d0", edgecolors="#15803d", linewidths=0.9)

    for branch in branches:
        color = palette[(branch["branch_id"] - 1) % len(palette)]
        nx.draw_networkx_nodes(
            graph,
            pos,
            nodelist=branch["seg_ids"],
            ax=ax,
            node_size=105,
            node_color=color,
            edgecolors="#111827",
            linewidths=0.35,
            alpha=0.78,
        )

    nx.draw_networkx_labels(graph, pos, labels={n: str(n) for n in graph.nodes}, ax=ax, font_size=5.8, font_color="#111827")

    handles = [
        plt.Line2D([0], [0], color="#16a34a", lw=2.4, label="主线走廊"),
        plt.Line2D([0], [0], color="#94a3b8", lw=1.2, label="分支内部连接"),
        plt.Line2D([0], [0], color="#dc2626", lw=1.6, linestyle="--", label="主线-分支接入"),
    ]
    for branch in branches:
        color = palette[(branch["branch_id"] - 1) % len(palette)]
        handles.append(
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=color,
                markeredgecolor="#111827",
                markersize=7,
                label=f"分支{branch['branch_id']} {branch['type']}({branch['seg_count']})",
            )
        )
    ax.legend(handles=handles, loc="upper right", fontsize=8.5, frameon=True)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(out_dir / "track_topology_classified.png", bbox_inches="tight", facecolor="white")
    fig.savefig(out_dir / "track_topology_classified.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="识别主线并分类分支")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="输出目录")
    args = parser.parse_args()

    graph, _ = build_weighted_graph(args.db)
    stations = load_station_anchors(args.db)
    main_start, main_end, main_path, main_cost = identify_main_path(graph, stations)
    stations_on_main = project_stations_on_main(main_path, stations)
    main_corridor = expand_main_corridor(graph, main_path, stations_on_main)
    branches = classify_branches(graph, main_corridor)

    write_outputs(args.out_dir, graph, main_start, main_end, main_path, main_corridor, main_cost, stations_on_main, branches)
    draw_classification_map(graph, main_corridor, branches, args.out_dir)

    print(f"主通道: {main_start['name']} -> {main_end['name']}")
    print(f"主通道 Seg 数: {len(main_path)} / 总 Seg 数: {graph.number_of_nodes()}")
    print(f"主线走廊 Seg 数: {len(main_corridor)} / 总 Seg 数: {graph.number_of_nodes()}")
    print(f"分支数量: {len(branches)}")
    print(f"输出目录: {args.out_dir}")


if __name__ == "__main__":
    main()
